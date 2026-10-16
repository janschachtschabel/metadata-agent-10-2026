"""
Prometheus metrics - served only on the internal port (infra.py).

prometheus_client's default registry already carries the process and Python
runtime metrics. On top come the few the operators of this service need:

- metadata_agent_http_requests_total / _duration_seconds: by method, route
  template (/nodes/{node_id}, never the id itself) and status
- metadata_agent_llm_requests_total / _duration_seconds: by provider and
  result - "ok" or the error code (LLM_AUTH_FORBIDDEN, LLM_RATE_LIMITED, …)
- metadata_agent_generations_total: /generate runs by status
  (success, partial, failed)

Counted per process; run one worker per container and scale with replicas.
"""

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

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
    "LLM calls by provider and result (ok or error code)",
    ["provider", "result"],
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
