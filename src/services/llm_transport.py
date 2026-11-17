"""One LLM call over HTTP: request body, the single auth header, retries, typed errors, metrics."""

import asyncio
import time
import httpx
from typing import TYPE_CHECKING, Any, Optional

from .llm_auth import b_api_auth, current_auth_source
from ..metrics import (
    LLM_DURATION,
    LLM_LAST_FAILURE,
    LLM_REQUESTS,
    count_llm_tokens,
)
from .llm_errors import (
    AUTH_SOURCE_LABELS,
    LLMAuthForbidden,
    LLMAuthRejected,
    LLMEmptyAnswer,
    LLMError,
    LLMModelNotFound,
    LLMRateLimited,
    LLMRequestRejected,
    LLMRouteMisconfigured,
    LLMRouteNotFound,
    LLMUnreachable,
    LLMUpstreamError,
    short_upstream_detail,
)
import logging

from .llm_gate import get_provider_gate

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger(__name__)


# Reasoning models take a different request body: 'max_completion_tokens'
# instead of 'max_tokens', no temperature other than the default, and they are
# the only ones that accept 'verbosity' and 'reasoning_effort' — gpt-4.1-mini
# answers 400 for those. Matched by prefix so a new model in the same family
# does not need a code change; anything unknown keeps the classic body.
_REASONING_MODEL_PREFIXES = ("gpt-5", "gpt-6", "o1", "o3", "o4")


def uses_reasoning_parameters(model: str) -> bool:
    """
    Whether this model expects the reasoning-style request body.

    Also behind the router's provider prefix: "openai/gpt-6-luna" is gpt-6-luna.
    A bare route name matches nothing and keeps the classic body.
    """
    name = str(model).lower()
    return name.startswith(_REASONING_MODEL_PREFIXES) or name.partition("/")[
        2
    ].startswith(_REASONING_MODEL_PREFIXES)


# The OpenAI prompt cache in explicit mode (gpt-6 models): a content part
# marked with it ends a cached prefix, and a request that carries one says so
# in "prompt_cache_options". Passed on unchanged by the B-API, also through its
# router (measured 2026-11-15).
CACHE_BREAKPOINT = {"mode": "explicit"}


def has_cache_breakpoint(messages: list[dict[str, Any]]) -> bool:
    """Whether a content part of these messages is marked as a cache breakpoint."""
    return any(
        isinstance(part, dict) and "prompt_cache_breakpoint" in part
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
    )


# Upper bound for one wait the gateway asks for. A per-minute quota answers
# Retry-After: 60 and more; waiting that long per retry would hold the whole
# request, and the editor in front of it, for minutes.
_MAX_RETRY_AFTER_SECONDS = 30.0
# How often a call that ended in a read timeout is sent in all.
READ_TIMEOUT_ATTEMPTS = 2


def retry_after_seconds(response: httpx.Response) -> float:
    """
    Seconds a 429 asks to wait (Retry-After in seconds), capped; 0 when absent.

    The fixed 1 s / 2 s backoff ignored it, so against a gateway that refills
    per minute every retry landed in the same closed window.
    """
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return 0.0
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_SECONDS)


def router_failure(status: int, body: str) -> Optional[str]:
    """
    What a B-API router error means, read from its message.

    The router answers configuration errors that never heal themselves partly
    with 503 (documented on the B-API routing page, findings B6/B7/B12), so the
    status alone cannot tell them from a short outage.

    Returns "route_not_found", "parameters", "misconfigured" (lasting - never
    retried), "rate_limited", "no_deployment" (may pass - retried), or None.
    """
    text = (body or "").lower()
    if status == 400 and "no route configured" in text:
        return "route_not_found"
    if status == 400 and "unsupported parameter" in text:
        return "parameters"
    if status == 503:
        # Throttling first: one throttled model in the attempts list is reason
        # enough to try again.
        if "rate_limited" in text:
            return "rate_limited"
        if "model pricing unavailable" in text or "not_eligible" in text:
            return "misconfigured"
        if "no deployment left" in text:
            return "no_deployment"
    return None


_LASTING_ROUTER_FAILURES = ("route_not_found", "parameters", "misconfigured")


def _after(attempts: int) -> str:
    return "nach 1 Versuch" if attempts == 1 else f"nach {attempts} Versuchen"


def _json_object(response: httpx.Response) -> Optional[dict[str, Any]]:
    """The body as a JSON object, or None when it is no JSON object."""
    try:
        data = response.json()
    except ValueError:  # json.JSONDecodeError and UnicodeDecodeError
        return None
    return data if isinstance(data, dict) else None


class LLMTransportMixin:
    """
    Sending one request to the configured provider (part of LLMService).

    Needs the attributes declared below, set by LLMService.__init__ (mypy
    checks every use against them).
    """

    settings: "Settings"
    provider: str
    model: str
    api_base: str
    api_key: str
    requires_custom_header: bool
    http_client: httpx.AsyncClient
    temperature: float
    max_tokens: int
    max_retries: int
    verbosity: str
    reasoning_effort: str
    limit_group: str
    max_concurrent_requests: int
    requests_per_second: float
    config_origin: str

    async def _call_llm(
        self,
        messages: list[dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        One LLM answer, counted and timed for /metrics (src/metrics.py).

        The time is what the caller waits: for a slot at the provider gate,
        for the request, its retries and the pauses between them.
        """
        start = time.monotonic()
        result = "ok"
        # Same rule as the call itself (b_api_auth), read before it is made.
        auth_source = (
            current_auth_source(self.settings) or "none"
            if self.requires_custom_header
            else "openai"
        )
        try:
            return await self._send_llm_request(messages, temperature, max_tokens)
        except LLMError as e:
            result = e.code
            raise
        except asyncio.CancelledError:
            # Stopped at the deadline of the run or because the caller went
            # away: no answer, so not "ok" (audit 14.11., L-36).
            result = "cancelled"
            raise
        except Exception:
            result = "error"
            raise
        finally:
            labels = (self.provider, result, auth_source, self.config_origin)
            LLM_REQUESTS.labels(*labels).inc()
            if result not in ("ok", "cancelled"):
                LLM_LAST_FAILURE.labels(*labels).set_to_current_time()
            LLM_DURATION.labels(self.provider).observe(time.monotonic() - start)

    async def _send_llm_request(
        self,
        messages: list[dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        attempts: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Call LLM API with proper provider configuration.
        Supports OpenAI and B-API (with X-API-KEY header).

        `attempts` overrides llm_max_retries, e.g. 1 for a call not worth waiting for.
        """
        attempts = attempts or self.max_retries
        request_body = self._request_body(messages, temperature, max_tokens)
        api_url, headers, auth_source = self._request_target()

        # Every LLM request in the process passes here — including content-type
        # detection and normalization, which do not go through the parallel
        # field extraction — so this is the one place where the gateway's budget
        # can actually be held. A retry is a request of its own and takes a slot.
        gate = get_provider_gate(
            self.limit_group, self.max_concurrent_requests, self.requests_per_second
        )

        last_status: Optional[int] = None
        last_detail = ""
        last_kind: Optional[str] = None
        for attempt in range(attempts):
            try:
                async with gate.semaphore:
                    await gate.rate_limiter.acquire()
                    response = await self.http_client.post(
                        api_url,
                        headers=headers,
                        json=request_body,
                    )

                answer = _json_object(response) if response.status_code == 200 else None
                if answer is not None:
                    count_llm_tokens(self.provider, answer)
                    return self._answer_or_raise(answer, auth_source)

                last_status = response.status_code
                last_detail, last_kind = self._failure_of(response)

                # A configuration error answers the same way on every try.
                if last_kind in _LASTING_ROUTER_FAILURES:
                    raise self._http_error(
                        last_status, last_detail, auth_source, last_kind, attempt + 1
                    )

                # Retry on transient errors (a 200 here had no JSON body)
                if response.status_code in [200, 429, 500, 502, 503, 504]:
                    if attempt < attempts - 1:
                        wait_time = (attempt + 1) * self.settings.llm_retry_delay
                        if response.status_code == 429:
                            wait_time = max(wait_time, retry_after_seconds(response))
                        logger.warning(
                            f"⚠️ LLM API returned {response.status_code}, retrying in {wait_time}s..."
                        )
                        await asyncio.sleep(wait_time)
                    continue

                # Not retryable: the same request would be refused again
                raise self._http_error(
                    last_status, last_detail, auth_source, attempts=attempt + 1
                )

            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.ReadError,
                httpx.RemoteProtocolError,
            ) as e:
                # A gateway that did not answer within the read timeout is
                # asked once more, not llm_max_retries times: three waits of
                # 90 s were 273 s for one field (audit 13.11., P-11).
                waited_in_vain = isinstance(e, httpx.ReadTimeout)
                last = attempt >= attempts - 1 or (
                    waited_in_vain and attempt >= READ_TIMEOUT_ATTEMPTS - 1
                )
                if not last:
                    await asyncio.sleep((attempt + 1) * self.settings.llm_retry_delay)
                    continue
                attempts = attempt + 1
                # The address stays in the log; clients get no internal hosts
                # (audit S-06).
                logger.error(
                    f"❌ No connection to {self.api_base} after {attempts} attempts: "
                    f"{type(e).__name__}"
                )
                raise LLMUnreachable(
                    f"Keine Verbindung zum KI-Dienst {_after(attempts)}: "
                    f"{type(e).__name__}",
                    auth_source=auth_source,
                ) from e

        raise self._http_error(
            last_status, last_detail, auth_source, last_kind, attempts
        )

    def _request_body(
        self,
        messages: list[dict[str, Any]],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> dict[str, Any]:
        """The body in the parameter style the model understands."""
        temp = temperature if temperature is not None else self.temperature
        tokens = max_tokens if max_tokens is not None else self.max_tokens

        request_body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        # Explicit mode: only the marked prefix is written to the cache. Left
        # at the default for every other call - their models may not know it.
        if has_cache_breakpoint(messages):
            request_body["prompt_cache_options"] = CACHE_BREAKPOINT

        # The explicit style describes the agent's route. Other providers -
        # also when a request picks one - are told apart by model name.
        style = (
            self.settings.llm_request_style
            if self.provider == "b-api-router"
            else "auto"
        )
        reasoning_body = (
            uses_reasoning_parameters(self.model)
            if style == "auto"
            else style == "reasoning"
        )
        if reasoning_body:
            # Measured against the B-API: these models answer 400 for
            # 'max_tokens' ("use max_completion_tokens instead") and for any
            # temperature other than the default, so it is left out entirely.
            request_body["max_completion_tokens"] = tokens
            if self.verbosity:
                request_body["verbosity"] = self.verbosity
            if self.reasoning_effort:
                request_body["reasoning_effort"] = self.reasoning_effort
        else:
            request_body["temperature"] = temp
            request_body["max_tokens"] = tokens
            # Thinking models in a classic route (qwen3.6) need "none", or they
            # spend the token budget on reasoning and return no text.
            router_effort = self.settings.b_api_router_reasoning_effort
            if self.provider == "b-api-router" and router_effort:
                request_body["reasoning_effort"] = router_effort
        return request_body

    def _request_target(self) -> tuple[str, dict[str, str], str]:
        """(URL, headers, auth source) of a request to the provider."""
        # Build headers based on provider. For the B-API the key is chosen per
        # request (forwarded caller key or own key, see llm_auth) - never kept
        # on this shared instance.
        headers = {"Content-Type": "application/json"}
        if self.requires_custom_header:
            auth_headers, auth_source = b_api_auth(self.settings)
            headers.update(auth_headers)
        else:
            auth_source = "openai"
            headers["Authorization"] = f"Bearer {self.api_key}"

        api_url = f"{self.api_base}/chat/completions"
        # The B-API caches word-identical requests, unmarked (b_api_ignore_cache).
        if self.requires_custom_header and self.settings.b_api_ignore_cache:
            api_url += "?ignore-caching=true"
        return api_url, headers, auth_source

    def _failure_of(self, response: httpx.Response) -> tuple[str, Optional[str]]:
        """(detail, kind) of an answer that brought no usable result."""
        if response.status_code == 200:
            # An HTML page from a proxy, a truncated body: the gateway failed
            # on the way - retried like a 5xx, reported as LLMUpstreamError.
            return response.headers.get("content-type", ""), "no_json"
        # Only the router words its errors this way; read elsewhere, a 400
        # "unsupported parameter" became a route error with advice
        # (LLM_REQUEST_STYLE) that other providers ignore.
        kind = (
            router_failure(response.status_code, response.text)
            if self.provider == "b-api-router"
            else None
        )
        return short_upstream_detail(response.text), kind

    def _answer_or_raise(
        self, data: dict[str, Any], auth_source: str
    ) -> dict[str, Any]:
        """
        A 200 without text is a failure, not "no value in the text".

        Thinking models return content null when the token budget went into
        reasoning (finish_reason "length"); counted as an empty field, that hid
        the failure entirely.
        """
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content")
        if content is None or (isinstance(content, str) and not content.strip()):
            raise LLMEmptyAnswer(
                f"Der KI-Dienst ({self.model}) lieferte keinen Text "
                f"(finish_reason={choice.get('finish_reason')}). Bei denkenden "
                "Modellen METADATA_AGENT_B_API_ROUTER_REASONING_EFFORT=none setzen "
                "oder METADATA_AGENT_LLM_MAX_TOKENS erhöhen.",
                auth_source=auth_source,
            )
        return data

    def _http_error(
        self,
        status: Optional[int],
        detail: str,
        auth_source: str,
        kind: Optional[str] = None,
        attempts: Optional[int] = None,
    ) -> LLMError:
        """
        Typed error for a non-200 answer, worded for the person who has to act.

        `attempts` is how often the request was really sent: a status that is
        not retried was reported as "nach 3 Versuchen" after one.
        """
        after = _after(attempts or self.max_retries)
        key = AUTH_SOURCE_LABELS.get(auth_source, auth_source)
        suffix = f": {detail}" if detail else ""
        context: dict[str, Any] = {
            "upstream_status": status,
            "auth_source": auth_source,
        }
        if kind == "route_not_found":
            return LLMRouteNotFound(
                f"Der B-API-Router kennt keine aktive Route '{self.model}' (HTTP 400). "
                f"Route anlegen bzw. aktivieren oder den Namen prüfen{suffix}",
                **context,
            )
        if kind == "parameters":
            return LLMRouteMisconfigured(
                f"Modell bzw. Route '{self.model}' lehnt die Anfrage-Parameter ab "
                f"(HTTP 400). METADATA_AGENT_LLM_REQUEST_STYLE passend zur "
                f"Modellfamilie setzen: reasoning für GPT-5/6, sonst classic{suffix}",
                **context,
            )
        if kind == "misconfigured":
            return LLMRouteMisconfigured(
                f"Der B-API-Router kann '{self.model}' so nicht bedienen (HTTP {status}): "
                f"Modell ohne Preis oder für diesen Endpunkt ungeeignet. Route bzw. "
                f"Modellnamen prüfen{suffix}",
                **context,
            )
        if kind == "rate_limited":
            return LLMRateLimited(
                f"Die Modelle hinter '{self.model}' werden weiterhin gedrosselt "
                f"(HTTP {status}, {key}){suffix}",
                **context,
            )
        if kind == "no_json":
            return LLMUpstreamError(
                f"Der KI-Dienst antwortete {after} mit "
                f"HTTP 200, aber ohne JSON ({detail or 'kein Content-Type'})",
                **context,
            )
        if kind == "no_deployment":
            return LLMUnreachable(
                f"Kein Modell der Route '{self.model}' ist erreichbar (HTTP {status}, "
                f"{after}){suffix}",
                **context,
            )
        if status == 401:
            return LLMAuthRejected(
                f"Der KI-Dienst hat die Anmeldung abgelehnt (HTTP 401; {key}): "
                "Schlüssel bzw. Token unbekannt, abgelaufen, der Aufruf beendet, oder "
                f"der Pfad liegt außerhalb der Scopes eines Delegations-Tokens{suffix}",
                upstream_status=status,
                auth_source=auth_source,
            )
        if status == 403:
            return LLMAuthForbidden(
                f"Der KI-Dienst hat den Zugriff verweigert (HTTP 403; {key}). Dem Schlüssel "
                f"fehlt eine Berechtigung – für den LLM-Endpunkt oder für Modell "
                f"{self.model}{suffix}",
                upstream_status=status,
                auth_source=auth_source,
            )
        if status == 404:
            logger.error(f"❌ 404 from {self.api_base} for model {self.model}")
            return LLMModelNotFound(
                f"Modell {self.model} oder Adresse des KI-Dienstes nicht gefunden "
                f"(HTTP 404){suffix}",
                upstream_status=status,
                auth_source=auth_source,
            )
        if status == 429:
            return LLMRateLimited(
                f"Der KI-Dienst drosselt die Anfragen weiterhin (HTTP 429, {key}){suffix}",
                upstream_status=status,
                auth_source=auth_source,
            )
        if status is not None and status >= 500:
            return LLMUpstreamError(
                f"Der KI-Dienst antwortete {after} mit HTTP {status}{suffix}",
                upstream_status=status,
                auth_source=auth_source,
            )
        return LLMRequestRejected(
            f"Der KI-Dienst hat die Anfrage abgelehnt (HTTP {status}){suffix}",
            upstream_status=status,
            auth_source=auth_source,
        )
