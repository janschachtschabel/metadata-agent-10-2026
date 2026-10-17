"""
Prometheus metrics - served only on the internal port (infra.py).

prometheus_client's default registry already carries the process and Python
runtime metrics. On top come the few the operators of this service need:

- metadata_agent_http_requests_total / _duration_seconds: by method, route
  template (/nodes/{node_id}, never the id itself) and status
- metadata_agent_llm_requests_total / _duration_seconds: by provider and
  result - "ok" or the error code (LLM_AUTH_FORBIDDEN, LLM_RATE_LIMITED, …);
  the counter also by auth_source (forwarded, static, openai, none) and config
  (default, or override when the request picked provider or model), so alerts
  can tell the operator's problems from the caller's
- metadata_agent_generations_total: /generate runs by status
  (success, partial, failed)
- metadata_agent_upstream_requests_total / _responses_total /
  _response_duration_seconds: every HTTP call to a service the agent depends
  on, by service (UPSTREAM_SERVICES) - counted when sent and again when an
  answer arrives, so sent minus answered is the calls that got none
- metadata_agent_llm_tokens_total: tokens from each LLM answer, by provider
  and kind (prompt, cached - the part of prompt read from the cache -,
  completion), the cache primer included

Counted per process; run one worker per container and scale with replicas.
Alert rules on these: helm/metadata-agent/files/alerts.yaml.
"""

import time
from typing import Any, Mapping

import httpx
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

from .config import Settings, get_settings
from .utils.request_id import propagate_trace

GEOCODER_URL = "https://photon.komoot.io/api"
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
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 90, 120, 300),
)
LLM_REQUESTS = Counter(
    "metadata_agent_llm_requests_total",
    "LLM calls by provider, result (ok or error code), key source and config origin",
    ["provider", "result", "auth_source", "config"],
)
LLM_DURATION = Histogram(
    "metadata_agent_llm_request_duration_seconds",
    "LLM call duration including retries, by provider",
    ["provider"],
    buckets=(0.5, 1, 2.5, 5, 10, 20, 30, 60, 120),
)
GENERATIONS = Counter(
    "metadata_agent_generations_total",
    "/generate runs by status (success, partial, failed)",
    ["status"],
)
UPSTREAM_REQUESTS = Counter(
    "metadata_agent_upstream_requests_total",
    "HTTP calls sent to a service the agent depends on",
    ["service"],
)
UPSTREAM_RESPONSES = Counter(
    "metadata_agent_upstream_responses_total",
    "Answers from a service the agent depends on, by HTTP status",
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
    "Tokens reported in LLM answers, by provider and kind (prompt, cached, completion)",
    ["provider", "kind"],
)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


class HttpMetricsMiddleware:
    """
    ASGI middleware: count and time every request by its route template.

    The router writes the matched route into the scope; reading it after the
    call keeps ids out of the labels. Unmatched paths share one label.
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
            method = scope.get("method", "")
            HTTP_REQUESTS.labels(method, route, str(status["code"])).inc()
            HTTP_DURATION.labels(method, route).observe(time.monotonic() - start)


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
    UPSTREAM_RESPONSES.labels(service, str(response.status_code)).inc()
    UPSTREAM_DURATION.labels(service).observe(time.monotonic() - started)


# For clients that talk to the agent's own services: tracing plus counting.
OWN_SERVICE_HOOKS = {
    "request": [propagate_trace, count_upstream_request],
    "response": [count_upstream_response],
}
# For third parties (the geocoder): counted, but no tracing headers.
THIRD_PARTY_HOOKS = {
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
    cached = details.get("cached_tokens") if isinstance(details, Mapping) else None
    for kind, value in (
        ("prompt", usage.get("prompt_tokens")),
        ("cached", cached),
        ("completion", usage.get("completion_tokens")),
    ):
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            LLM_TOKENS.labels(provider, kind).inc(value)
