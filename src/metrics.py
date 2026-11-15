"""
Prometheus metrics - served only on the internal port (infra.py).

prometheus_client's default registry already carries the process and Python
runtime metrics. On top come the few the operators of this service need:

- metadata_agent_http_requests_total / _duration_seconds: by method, route
  template (/nodes/{node_id}, never the id itself) and status
- metadata_agent_llm_requests_total / _duration_seconds: by provider and
  result - "ok", "cancelled" (stopped at the deadline or because the client
  left) or the error code (LLM_AUTH_FORBIDDEN, LLM_RATE_LIMITED, …);
  the counter also by auth_source (forwarded, static, openai, none) and config
  (default, or override when the request picked provider or model), so alerts
  can tell the operator's problems from the caller's
- metadata_agent_llm_last_failure_timestamp_seconds (labels of the LLM
  counter) and metadata_agent_http_last_internal_error_timestamp_seconds (by
  route): when a call last ended in an error and when the agent last answered
  500 - what lets the critical alerts page on a first failure
- metadata_agent_generations_total: /generate runs by status
  (GENERATION_STATUSES)
- metadata_agent_upstream_requests_total / _responses_total /
  _response_duration_seconds: every HTTP call to a service the agent depends
  on, by service (UPSTREAM_SERVICES) - counted when sent and again when an
  answer arrives, so sent minus answered is the calls that got none; the
  answers also by status (for "other" by status class: 2xx, 4xx …)
- metadata_agent_llm_tokens_total: tokens from each LLM answer, by provider
  and kind (prompt, cached - the part of prompt read from the cache -,
  completion), the cache primer included

Counted per process; run one worker per container and scale with replicas.
Alert rules on these: helm/metadata-agent/files/alerts.yaml.
"""

import time
from typing import Any, Callable, Mapping
from urllib.parse import quote

import httpx
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.responses import Response

from .config import PHOTON_URL, Settings, get_settings
from .utils.logging_setup import log_request
from .utils.request_id import propagate_trace

GEOCODER_URL = PHOTON_URL
# Fixed label values: a host or path in a label would grow with traffic.
UPSTREAM_SERVICES = ("llm", "text-extraction", "repository", "geocoder", "other")

HTTP_REQUESTS = Counter(
    "metadata_agent_http_requests_total",
    "HTTP requests by method, route template and status",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "metadata_agent_http_request_duration_seconds",
    "HTTP request duration by method and route template",
    ["method", "route"],
    # /generate runs 12-90 s depending on the model; the default buckets end at 10 s.
    # 150-270: the alert on slow runs reads the share above 270 s; without
    # them a p95 was interpolated between 120 and 300 s (audit 14.11., O-33).
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 90, 120)
    + (150, 180, 210, 240, 270, 300),
)
LLM_REQUESTS = Counter(
    "metadata_agent_llm_requests_total",
    "LLM calls by provider, result (ok, cancelled or error code), key source "
    "and config origin",
    ["provider", "result", "auth_source", "config"],
)
LLM_DURATION = Histogram(
    "metadata_agent_llm_request_duration_seconds",
    "Time an LLM call takes for its caller: wait for a slot at the provider "
    "gate, request, retries and pauses between them; by provider",
    ["provider"],
    buckets=(0.5, 1, 2.5, 5, 10, 20, 30, 60, 120),
)
# When the agent last failed, for the critical alerts (alerts.yaml). They page
# on the first failure, and increase() does not count a series' first value:
# `time() - <gauge> < 600` sees it. Unlike a look-back for series that are
# new, an old failure does not page again after a scrape gap, however long
# (audit 14.11., O-34). The LLM gauge has the labels of LLM_REQUESTS - the
# rules select the operator's own key and the configured model by them.
LLM_LAST_FAILURE = Gauge(
    "metadata_agent_llm_last_failure_timestamp_seconds",
    "Unix time of the last LLM call that ended in an error, by provider, error "
    "code, key source and config origin",
    ["provider", "result", "auth_source", "config"],
)
HTTP_LAST_INTERNAL_ERROR = Gauge(
    "metadata_agent_http_last_internal_error_timestamp_seconds",
    "Unix time of the last answer with status 500, by route template",
    ["route"],
)
GENERATIONS = Counter(
    "metadata_agent_generations_total",
    "/generate runs by status (success, partial, failed, cancelled, refused)",
    ["status"],
)
# success, partial, failed: how a run ended. cancelled: the client gave it up.
# refused: the pod did not take it on (503 TOO_MANY_GENERATIONS). The alerts
# count only the first three as runs (alerts.yaml).
GENERATION_STATUSES = ("success", "partial", "failed", "cancelled", "refused")


def count_generation(status: object) -> None:
    """
    Count a /generate run. A status outside GENERATION_STATUSES counts as
    failed: under a label of its own ("unknown") no alert would see the run.
    """
    GENERATIONS.labels(status if status in GENERATION_STATUSES else "failed").inc()


UPSTREAM_REQUESTS = Counter(
    "metadata_agent_upstream_requests_total",
    "HTTP calls sent to a service the agent depends on",
    ["service"],
)
UPSTREAM_RESPONSES = Counter(
    "metadata_agent_upstream_responses_total",
    "Answers from a service the agent depends on, by HTTP status (other: by class)",
    ["service", "status"],
)
UPSTREAM_DURATION = Histogram(
    "metadata_agent_upstream_response_duration_seconds",
    "Time until a service's answer arrives (headers), by service",
    ["service"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 90),
)
LLM_TOKENS = Counter(
    "metadata_agent_llm_tokens_total",
    "Tokens reported in LLM answers, by provider and kind (prompt, cached, cache_write, completion)",
    ["provider", "kind"],
)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# The results the critical alerts watch (alerts.yaml; tests/test_metrics_labels.py
# keeps both in step).
CRITICAL_LLM_RESULTS = (
    "LLM_AUTH_MISSING",
    "LLM_AUTH_REJECTED",
    "LLM_AUTH_FORBIDDEN",
    "LLM_ROUTE_NOT_FOUND",
    "LLM_ROUTE_MISCONFIGURED",
    "LLM_MODEL_NOT_FOUND",
)


def precreate_alert_series(settings: Settings) -> None:
    """
    Create the series of the critical LLM alerts at 0 (audit OPS-06).

    A labelled series appears with its first .inc(), and increase() does not
    count that first value: a key refusal right after a pod start would never
    page. Only the configured provider with config="default" - the label sets
    the critical rules select - and the key sources it can have.
    """
    if settings.llm_provider.startswith("b-api"):
        sources: tuple[str, ...] = ("static", "forwarded", "none")
    else:
        sources = ("openai",)
    for result in CRITICAL_LLM_RESULTS:
        for source in sources:
            LLM_REQUESTS.labels(settings.llm_provider, result, source, "default")


# Methods a client may send; anything else shares one label value (OPS-14).
HTTP_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}
)


# No route is longer; a path of many kilobytes is noise in the log.
MAX_LOGGED_PATH = 2000


def _logged_path(scope) -> str:
    """
    The path as the client sent it, still percent-encoded.

    scope["path"] is decoded: %0A would break the log line in two.
    """
    raw = scope.get("raw_path")
    path = (
        quote(scope.get("path", ""))
        if raw is None
        else raw.decode("latin-1").partition("?")[0]
    )
    if len(path) > MAX_LOGGED_PATH:
        return path[:MAX_LOGGED_PATH] + "…"
    return path


class HttpMetricsMiddleware:
    """
    ASGI middleware: count, time and log every request.

    The router writes the matched route into the scope; reading it after the
    call keeps ids out of the labels. Unmatched paths share one label. The log
    line names the path that was called, without the query string.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        status = {"code": 500}

        async def send_with_status(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        start = time.monotonic()
        try:
            await self.app(scope, receive, send_with_status)
        finally:
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            # The method comes from the client: unbounded on unmatched paths.
            method = scope.get("method", "")
            if method not in HTTP_METHODS:
                method = "OTHER"
            seconds = time.monotonic() - start
            HTTP_REQUESTS.labels(method, route, str(status["code"])).inc()
            if status["code"] == 500:
                HTTP_LAST_INTERNAL_ERROR.labels(route).set_to_current_time()
            HTTP_DURATION.labels(method, route).observe(seconds)
            log_request(method, _logged_path(scope), status["code"], seconds)


# ------------------------------------------------------------ upstream calls


def _under(url: str, prefix: str) -> bool:
    """url starts with prefix, up to a path boundary."""
    if not prefix or not url.startswith(prefix):
        return False
    return len(url) == len(prefix) or url[len(prefix)] in "/?"


def upstream_service(url: str, settings: Settings) -> str:
    """The service a URL belongs to - one of UPSTREAM_SERVICES."""
    prefixes = (
        (settings.b_api_openai_base, "llm"),
        (settings.b_api_academiccloud_base, "llm"),
        (settings.b_api_router_base, "llm"),
        (settings.openai_api_base, "llm"),
        (settings.text_extraction_api_url, "text-extraction"),
        (settings.repository_url, "repository"),
        (GEOCODER_URL, "geocoder"),
        (settings.geocoder_api_url, "geocoder"),
    )
    # The longest prefix wins: a service may live below another's address.
    for prefix, service in sorted(prefixes, key=lambda p: -len(p[0])):
        if _under(url, prefix):
            return service
    return "other"


async def count_upstream_request(request: httpx.Request) -> None:
    """httpx request hook: count the call and note when it left."""
    service = upstream_service(str(request.url), get_settings())
    request.extensions["metadata_agent_upstream"] = (service, time.monotonic())
    UPSTREAM_REQUESTS.labels(service).inc()


async def count_upstream_response(response: httpx.Response) -> None:
    """httpx response hook: count the answer and how long it took."""
    noted = response.request.extensions.get("metadata_agent_upstream")
    if noted is None:  # sent by a client without the request hook
        return
    service, started = noted
    status = str(response.status_code)
    if service == "other":
        # Any server a caller names answers here, with any code it likes: the
        # class bounds the label (audit 14.11.). No alert reads "other".
        status = f"{status[:1]}xx"
    UPSTREAM_RESPONSES.labels(service, status).inc()
    UPSTREAM_DURATION.labels(service).observe(time.monotonic() - started)


# For clients that talk to the agent's own services: tracing plus counting.
OWN_SERVICE_HOOKS: dict[str, list[Callable[..., Any]]] = {
    "request": [propagate_trace, count_upstream_request],
    "response": [count_upstream_response],
}
# For third parties (Photon called directly): counted, but no tracing headers.
THIRD_PARTY_HOOKS: dict[str, list[Callable[..., Any]]] = {
    "request": [count_upstream_request],
    "response": [count_upstream_response],
}


# ------------------------------------------------------------ tokens


def count_llm_tokens(provider: str, answer: Mapping[str, Any]) -> None:
    """Count the usage block of an OpenAI-style answer; absent fields count nothing."""
    usage = answer.get("usage")
    if not isinstance(usage, Mapping):
        return
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, Mapping):
        details = {}
    # "cached" was read from the prompt cache (a tenth of the input price),
    # "cache_write" was written to it (1.25 times); both are part of "prompt".
    for kind, value in (
        ("prompt", usage.get("prompt_tokens")),
        ("cached", details.get("cached_tokens")),
        ("cache_write", details.get("cache_write_tokens")),
        ("completion", usage.get("completion_tokens")),
    ):
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            LLM_TOKENS.labels(provider, kind).inc(value)
