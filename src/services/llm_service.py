"""LLM service for parallel metadata extraction."""

import asyncio
import httpx
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Iterator, Optional

from ..config import MAX_WORKERS, get_settings
from ..metrics import OWN_SERVICE_HOOKS, THIRD_PARTY_HOOKS
from ..utils.tls_context import shared_tls_context
from ..utils.vocabulary import is_closed_vocabulary
from .llm_errors import (
    LLMAnswerTruncated,
    LLMEmptyAnswer,
    LLMError,
)
import logging

# Re-exported: callers and tests import these from llm_service, as before 3.0.0.
from .llm_content_type import ContentTypeDetection as ContentTypeDetection
from .llm_content_type import ContentTypeMixin
from .llm_dates_numbers import DateNumberMixin, impossible_dates
from .llm_gate import ProviderGate as ProviderGate
from .llm_gate import RateLimiter as RateLimiter
from .llm_gate import get_provider_gate as get_provider_gate
from .llm_gate import reset_provider_gates as reset_provider_gates
from .llm_prompts import PromptMixin
from .llm_transport import LLMTransportMixin
from .llm_transport import retry_after_seconds as retry_after_seconds
from .llm_transport import router_failure as router_failure
from .llm_transport import uses_reasoning_parameters as uses_reasoning_parameters
from .llm_values import ValueNormalizerMixin
from .vocabulary_matching import is_valid_answer

logger = logging.getLogger(__name__)


# Field calls end this long before the deadline of the run: what follows them
# (normalising, geocoding, the answer) needs the rest.
FIELD_RESERVE_SECONDS = 5.0

# Providers whose prompt cache the primer feeds (see LLMService._prime_text_cache).
PROMPT_CACHE_PROVIDERS = ("b-api-openai", "openai")
# Models that take an explicit cache breakpoint (llm_cache_breakpoint "auto").
_BREAKPOINT_MODEL_PREFIXES = ("gpt-6",)
# The primer's answer is thrown away; keep it as short as the API allows.
_PRIMER_MAX_TOKENS = 16
# Roughly the 1,024 tokens below which the OpenAI prompt cache stores nothing.
_PRIMER_MIN_TEXT_CHARS = 4000


# What extract_field has to say about an answer it left out (an impossible
# date). A context variable: the field tasks of a run inherit it, concurrent
# runs each have their own, and the signature of extract_field stays.
_FIELD_NOTES: ContextVar[Optional[list[str]]] = ContextVar("field_notes", default=None)


@contextmanager
def field_notes() -> Iterator[list[str]]:
    """Collect the notes of the extract_field calls made inside."""
    notes: list[str] = []
    token = _FIELD_NOTES.set(notes)
    try:
        yield notes
    finally:
        _FIELD_NOTES.reset(token)


def _note_dropped_dates(field: dict[str, Any], answer: Any) -> None:
    """A date the model wrote and the agent did not take over is named, not lost."""
    notes = _FIELD_NOTES.get()
    if notes is None or field.get("system", {}).get("datatype") != "date":
        return
    for written in impossible_dates(answer):
        notes.append(
            f"{field.get('id', 'unknown')}: '{written[:40]}' ist kein Datum in der "
            "Lesart Tag/Monat/Jahr und wurde nicht übernommen."
        )


class LLMService(
    LLMTransportMixin,
    PromptMixin,
    ValueNormalizerMixin,
    DateNumberMixin,
    ContentTypeMixin,
):
    """Service for LLM-based metadata extraction with parallel processing."""

    def __init__(
        self, llm_provider: Optional[str] = None, llm_model: Optional[str] = None
    ):
        """
        Initialize LLM service.

        Args:
            llm_provider: Override default provider ('openai', 'b-api-openai', 'b-api-academiccloud')
            llm_model: Override default model for the provider
        """
        settings = get_settings()
        self.settings = settings

        # Get config with optional overrides
        self.llm_config = settings.get_llm_config(
            provider_override=llm_provider, model_override=llm_model
        )

        # Provider info
        self.provider = self.llm_config["provider"]
        self.api_key = self.llm_config["api_key"]
        self.api_base = self.llm_config["api_base"]
        self.model = self.llm_config["model"]
        self.temperature = self.llm_config["temperature"]
        self.requires_custom_header = self.llm_config["requires_custom_header"]
        self.max_tokens = settings.llm_max_tokens
        self.max_retries = settings.llm_max_retries
        self.verbosity = settings.llm_verbosity
        self.reasoning_effort = settings.llm_reasoning_effort

        # What this provider's gateway tolerates — enforced process-wide, see
        # get_provider_gate().
        self.limit_group = self.llm_config["limit_group"]
        self.max_concurrent_requests = self.llm_config["max_concurrent_requests"]
        self.requests_per_second = self.llm_config["requests_per_second"]

        # "override" when a request picked its own provider or model: its
        # failures are the caller's, not the operator's (alerts.yaml).
        configured = settings.get_llm_config()
        self.config_origin = (
            "default"
            if (self.provider, self.model)
            == (configured["provider"], configured["model"])
            else "override"
        )

        # HTTP client for B-API (requires custom headers).
        #
        # The read timeout is what bounds a single LLM call, so it comes from
        # request_timeout: a reasoning model on a long text needs more than the
        # library default. Connecting is a separate, much shorter budget — an
        # unreachable gateway must fail fast instead of occupying a slot at the
        # provider gate for the full request timeout.
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(settings.request_timeout), connect=10.0),
            # A request that picks its own provider or model builds a client
            # of its own; the TLS context is the costly part (audit 14.11., L-41).
            verify=shared_tls_context(),
            # Tracing headers only to the B-API (the agent's own services);
            # native OpenAI is a third party (UEBERGABE section 2).
            event_hooks=(
                OWN_SERVICE_HOOKS if self.requires_custom_header else THIRD_PARTY_HOOKS
            ),
        )

        logger.info(f"🤖 LLM Service initialized: {self.provider}")
        logger.info(f"   Model: {self.model}")
        logger.info(f"   API Base: {self.api_base}")

    async def close(self):
        """Close the HTTP client."""
        if self.http_client:
            await self.http_client.aclose()

    async def extract_field(
        self,
        field: dict[str, Any],
        text: str,
        existing_value: Any = None,
        language: str = "de",
        retry_count: int = 0,
        last_attempt: bool = False,
    ) -> tuple[str, Any, "Optional[str | LLMError]"]:
        """
        Extract a single field value from text with retry logic.

        `retry_count` and `last_attempt` are set by the method itself when it
        asks again: an answer outside a closed vocabulary up to
        llm_max_retries times, a required field without a value once.

        Returns: (field_id, extracted_value, error) - error is None, a message,
        or the LLMError of a failed call (str() gives its message).

        Raises:
            LLMError: a fatal one (refused key, exhausted quota, no gateway) -
                every further field would fail the same way.
        """
        field_id = field.get("id", "unknown")
        max_retries = self.settings.llm_max_retries

        try:
            # Build prompt with retry hints if this is a retry
            prompt = self._build_extraction_prompt(
                field,
                text,
                existing_value,
                language,
                retry_count,
                last_attempt=last_attempt or retry_count >= max_retries,
            )

            response = await self._call_llm(
                messages=[
                    *self._text_messages(text, language),
                    {"role": "user", "content": prompt},
                ],
            )

            choice = response.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "")
            if choice.get("finish_reason") == "length":
                raise LLMAnswerTruncated(
                    f"Die Antwort des KI-Diensts ({self.model}) für {field_id} wurde "
                    "an der Tokengrenze abgeschnitten (finish_reason=length). "
                    "METADATA_AGENT_LLM_MAX_TOKENS erhöhen."
                )

            # Parse JSON response
            result = self._parse_json_response(content)
            extracted_value = result.get("value") if result else None
            # A blank answer means "no value", like null - not an invalid concept
            # worth llm_max_retries more calls.
            if isinstance(extracted_value, str) and not extracted_value.strip():
                extracted_value = None

            # Normalize the extracted value
            answer = extracted_value
            if extracted_value is not None:
                normalized = self._normalize_value(extracted_value, field)

                # An answer outside a closed vocabulary: ask again with the hint
                # about invalid values; after the last attempt keep what matched.
                vocabulary = field.get("system", {}).get("vocabulary", {})
                if is_closed_vocabulary(vocabulary):
                    valid = is_valid_answer(normalized, vocabulary)
                    if not valid and retry_count < max_retries:
                        return await self.extract_field(
                            field, text, existing_value, language, retry_count + 1
                        )
                    if isinstance(normalized, list):
                        normalized = [v for v in normalized if v is not None]

                extracted_value = normalized

            # No value for a required field: ask once more, and say that it
            # is the last time - llm_max_retries more calls for a value the
            # text does not have were four calls per field. In a closed
            # vocabulary null says that no concept fits: no second call
            # (audit 14.11., L-46).
            system = field.get("system", {})
            if (
                extracted_value is None
                and retry_count == 0
                and system.get("required", False)
                and not is_closed_vocabulary(system.get("vocabulary", {}))
            ):
                return await self.extract_field(
                    field, text, existing_value, language, 1, last_attempt=True
                )

            _note_dropped_dates(field, answer)
            return (field_id, extracted_value, None)

        except LLMError as e:
            # _call_llm already retried what can be retried; another round here
            # would only repeat the same refusal or outage.
            # A fatal one (refused key, quota, no gateway) ends the extraction.
            if e.fatal:
                raise
            return (field_id, None, e)

        except Exception:
            # Transport and gateway failures arrive as LLMError (retried in
            # _call_llm). Anything else is a fault in the agent: asking the model
            # again would only repeat it at the price of more calls (audit P-06).
            logger.exception(f"❌ Extracting {field_id} failed")
            return (field_id, None, f"Internal error while extracting {field_id}")

    async def _prime_text_cache(self, text: str, language: str) -> None:
        """
        Send the shared prefix once before the field calls fan out.

        Measured through the B-API (2026-10-16): the prompt cache only hits
        where an earlier prompt ended. Without a call that ends exactly after
        the text, the ~50 field calls of one extraction wrote the text to the
        cache 50 times and read it back never. After this call they read it at
        a tenth of the input price.

        Only for OpenAI models (_primes_prompt_cache): AcademicCloud shares a small per-minute
        quota that one more call per page would eat into. Bypasses the LLM
        request metrics on purpose — a primer answer is irrelevant, and an
        empty one is no failure; its tokens and its HTTP call are counted. Errors are only logged: a refused key or a dead gateway
        shows up on the first field call right after.
        """
        if not self._primes_prompt_cache():
            return
        try:
            # One attempt: waiting out retries would only delay the field calls.
            await self._send_llm_request(
                self._text_messages(text, language), None, _PRIMER_MAX_TOKENS, 1
            )
        except LLMEmptyAnswer:
            # The usual outcome: 16 tokens go into reasoning and no text comes
            # back (finish_reason "length"). The prompt was read, so it is cached.
            pass
        except LLMError as e:  # incl. a 200 without a JSON body (LLMUpstreamError)
            logger.info(f"Prompt cache primer skipped: {e.code}")

    @asynccontextmanager
    async def text_cache_primer(
        self, text: str, language: str, start: bool = True
    ) -> AsyncIterator["Optional[asyncio.Future[None]]"]:
        """
        Start the primer now, for a run that has another call to wait for
        before its field calls: the content-type detection needs nothing but
        the text either, and the primer took 1.1 s in which nothing else was
        on its way (measured through the B-API, 2026-11-15).

        Yields the primer on its way for extract_fields_parallel(primer=…), or
        None where none is sent: `start` is False (nothing to wait for first),
        or see _prime_text_cache. A primer nobody waited for - the run ended
        early, one field was left - is stopped on leaving.
        """
        primer: Optional[asyncio.Future[None]] = None
        if start and self._primes_prompt_cache() and len(text) > _PRIMER_MIN_TEXT_CHARS:
            primer = asyncio.ensure_future(self._prime_text_cache(text, language))
        try:
            yield primer
        finally:
            if primer is not None:
                primer.cancel()  # no effect on one that is done
                await asyncio.gather(primer, return_exceptions=True)

    async def _text_cache_primed(
        self, text: str, language: str, primer: "Optional[asyncio.Future[None]]"
    ) -> None:
        """Wait for the primer on its way, or send it now (extract_fields_parallel)."""
        if primer is not None:
            await primer  # started alongside the type detection
        # A short text stays below the provider's ~1,024-token cache minimum.
        elif len(text) > _PRIMER_MIN_TEXT_CHARS:
            await self._prime_text_cache(text, language)

    def _primes_prompt_cache(self) -> bool:
        """
        Whether this provider and model share the OpenAI prompt cache.

        The router's default "openai/…" model is an OpenAI model with the same
        cache (audit P-02); a route name may stand for AcademicCloud models and
        their shared quota, so it gets no extra call.
        """
        if self.provider in PROMPT_CACHE_PROVIDERS:
            return True
        return self.provider == "b-api-router" and str(self.model).startswith("openai/")

    def _marks_cache_breakpoint(self) -> bool:
        """
        Whether the page text ends with an explicit cache breakpoint
        (llm_cache_breakpoint; PromptMixin._text_messages).

        Never outside the OpenAI prompt cache: other models answer 400 for a
        parameter they do not know.
        """
        mode = self.settings.llm_cache_breakpoint
        if mode == "off" or not self._primes_prompt_cache():
            return False
        name = str(self.model).lower().removeprefix("openai/")
        return mode == "on" or name.startswith(_BREAKPOINT_MODEL_PREFIXES)

    async def extract_fields_parallel(
        self,
        fields: list[dict[str, Any]],
        text: str,
        existing_metadata: Optional[dict[str, Any]] = None,
        language: str = "de",
        max_workers: int = MAX_WORKERS,
        deadline: Optional[float] = None,
        primer: "Optional[asyncio.Future[None]]" = None,
    ) -> dict[str, Any]:
        """
        Extract multiple fields in parallel with worker limit.

        `deadline` is the loop time the whole run must end by. Field calls
        still running FIELD_RESERVE_SECONDS before it are stopped and named in
        "timed_out"; what is done by then is the result (audit 13.11., P-11).
        "warnings" names answers that were left out (an impossible date).
        `primer` is a cache primer already on its way (text_cache_primer);
        without it the primer is sent here, before the field calls.

        Returns: dict with field_id -> value mappings
        """
        # More workers than the gateway lets through at once buys nothing — the
        # gate in _call_llm would queue them anyway. Capping here keeps the
        # reported worker count honest instead of promising a parallelism that
        # cannot happen.
        workers = min(max_workers, self.max_concurrent_requests)
        if workers < max_workers:
            logger.info(
                f"⚙️ Workers: {max_workers} requested → {workers} "
                f"({self.limit_group} allows {self.max_concurrent_requests} in flight)"
            )
        semaphore = asyncio.Semaphore(workers)
        results = {}
        errors = []
        first_error: list[LLMError] = []
        fatal: list[LLMError] = []
        skipped = 0

        async def extract_with_semaphore(field: dict[str, Any]) -> None:
            nonlocal skipped
            async with semaphore:
                # After a fatal error (refused key, exhausted quota, no gateway)
                # every further call would fail the same way: do not send it.
                if fatal:
                    skipped += 1
                    return
                field_id = field.get("id", "unknown")
                existing_value = (
                    existing_metadata.get(field_id) if existing_metadata else None
                )

                try:
                    field_id, value, error = await self.extract_field(
                        field, text, existing_value, language
                    )
                except LLMError as e:
                    if not fatal:
                        fatal.append(e)
                        logger.error(
                            f"⛔ Extraction stopped: {e.code} ({e.upstream_status})"
                        )
                    else:
                        skipped += 1
                    return

                if error:
                    errors.append(f"{field_id}: {error}")
                    if isinstance(error, LLMError) and not first_error:
                        first_error.append(error)
                elif value is not None:
                    results[field_id] = value

        # One field cannot read back what the primer wrote.
        if len(fields) > 1:
            await self._text_cache_primed(text, language, primer)

        with field_notes() as notes:
            timed_out = await _await_field_tasks(
                {
                    asyncio.ensure_future(extract_with_semaphore(f)): f.get(
                        "id", "unknown"
                    )
                    for f in fields
                },
                deadline,
            )
        if timed_out:
            logger.warning(
                f"⏱️ {len(timed_out)} field call(s) stopped at the deadline: {timed_out}"
            )

        return {
            "values": results,
            "errors": errors,
            "first_error": first_error[0] if first_error else None,
            "fatal_error": fatal[0] if fatal else None,
            "skipped": skipped,
            "timed_out": timed_out,
            "warnings": notes,
        }


async def _await_field_tasks(
    tasks: dict[asyncio.Future, str], deadline: Optional[float]
) -> list[str]:
    """
    Wait for the field tasks (task -> field id); the ids of those that were
    still running FIELD_RESERVE_SECONDS before `deadline` and were stopped.
    """
    # Nothing left to extract (a complete record): asyncio.wait refuses an
    # empty set, which was a 500 (audit 14.11., L-31).
    if not tasks:
        return []
    try:
        if deadline is None:
            await asyncio.gather(*tasks)
            return []
        loop = asyncio.get_running_loop()
        left = max(0.0, deadline - FIELD_RESERVE_SECONDS - loop.time())
        done, pending = await asyncio.wait(tasks, timeout=left)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()  # a fault in a field task, as gather raised it
        return [tasks[task] for task in tasks if task in pending]
    except BaseException:
        # gather re-raises the first fault and leaves the other field tasks
        # running: LLM calls nobody waits for. Stop them, then re-raise.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


_llm_service: Optional[LLMService] = None


def get_llm_service(
    llm_provider: Optional[str] = None, llm_model: Optional[str] = None
) -> LLMService:
    """
    Get LLM service instance.

    If provider or model overrides are specified, creates a new instance.
    Otherwise returns the cached default instance.
    """
    global _llm_service

    # If overrides specified, create new instance with those settings
    if llm_provider is not None or llm_model is not None:
        return LLMService(llm_provider=llm_provider, llm_model=llm_model)

    # Return cached default instance
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
