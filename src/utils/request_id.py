"""
One id per request, and the tracing headers that come with it.

The id lets a failure be quoted and found in the log. It is the incoming
`x-client-trace-id` (the header Team 6 uses to filter logs across services),
else an incoming `X-Request-ID`, else generated - incoming values only when
they are plain tokens. Every response carries it as `X-Request-ID`; error
bodies repeat it as `error.request_id` (main.py).

Tracing is B3 as Envoy/Istio expect it: the B3 headers, `x-request-id` and
`x-client-trace-id` of a request are passed on unchanged to the agent's own
services (B-API, text extraction, repository) through TRACE_EVENT_HOOKS -
never to third parties such as the geocoder or pages being screenshotted.
"""

import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Mapping, Optional

import httpx

HEADER = "X-Request-ID"
_HEADER_KEY = HEADER.lower().encode("latin-1")
CLIENT_TRACE_HEADER = "x-client-trace-id"

# Forwarded as received; the order is the order of preference for the id.
TRACE_HEADERS = (
    CLIENT_TRACE_HEADER,
    "x-request-id",
    "x-b3-traceid",
    "x-b3-spanid",
    "x-b3-parentspanid",
    "x-b3-sampled",
    "x-b3-flags",
    "b3",
)
# Anything else could forge log lines or smuggle content into other requests.
_PLAIN_TOKEN = re.compile(r"[A-Za-z0-9._-]{1,64}")
_TRACE_VALUE = re.compile(r"[A-Za-z0-9._-]{1,128}")

_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_trace: ContextVar[Optional[dict[str, str]]] = ContextVar("trace", default=None)


def current_request_id() -> Optional[str]:
    """The id of the request being handled; None outside of one."""
    return _request_id.get()


def trace_headers() -> dict[str, str]:
    """The tracing headers to pass on from the request being handled."""
    return dict(_trace.get() or {})


@contextmanager
def traced(incoming: Mapping[str, str]) -> Iterator[str]:
    """Set id and tracing headers from a request's headers; yields the id."""
    headers = {k.lower(): v for k, v in incoming.items()}
    trace = {
        name: headers[name]
        for name in TRACE_HEADERS
        if _TRACE_VALUE.fullmatch(headers.get(name, ""))
    }
    request_id = (
        next(
            (
                headers[name]
                for name in (CLIENT_TRACE_HEADER, "x-request-id")
                if _PLAIN_TOKEN.fullmatch(headers.get(name, ""))
            ),
            None,
        )
        or uuid.uuid4().hex[:16]
    )
    # Downstream log lines can be matched with the agent's own.
    trace.setdefault("x-request-id", request_id)

    id_token = _request_id.set(request_id)
    trace_token = _trace.set(trace)
    try:
        yield request_id
    finally:
        _trace.reset(trace_token)
        _request_id.reset(id_token)


async def propagate_trace(request: httpx.Request) -> None:
    """httpx request hook: add the tracing headers the request does not set itself."""
    for name, value in trace_headers().items():
        if name not in request.headers:
            request.headers[name] = value


# For every client that talks to the agent's own services.
TRACE_EVENT_HOOKS = {"request": [propagate_trace]}


class RequestIdMiddleware:
    """
    ASGI middleware: assign the id, keep the tracing headers, answer with the id.

    Plain ASGI like ForwardedAuthorizationMiddleware, so both are set in the
    very task that serves the request and every log line of it can use them.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        incoming = {
            k.decode("latin-1"): v.decode("latin-1")
            for k, v in reversed(scope.get("headers", []))
        }

        with traced(incoming) as request_id:

            async def send_with_id(message):
                if message["type"] == "http.response.start":
                    headers = [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k != _HEADER_KEY
                    ]
                    headers.append((_HEADER_KEY, request_id.encode("latin-1")))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_with_id)
