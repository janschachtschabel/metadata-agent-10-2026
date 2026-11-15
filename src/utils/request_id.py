"""
One id per request, and the tracing headers that come with it.

The id lets a failure be quoted and found in the log. It is the incoming
`x-client-trace-id` (the header Team 6 uses to filter logs across services),
else an incoming `X-Request-ID`, else the B3 trace id - incoming values only
when they are plain tokens. Every response carries it as `X-Request-ID`; error
bodies repeat it as `error.request_id` (main.py).

Tracing is B3 as Envoy/Istio expect it: the B3 headers, `x-request-id` and
`x-client-trace-id` of a request are passed on unchanged to the agent's own
services (B-API, text extraction, repository) through TRACE_EVENT_HOOKS, or
OWN_SERVICE_HOOKS (metrics.py), which also counts the calls - never to third
parties such as Photon itself or pages being screenshotted (the geocoder
called through the B-API is a call to the B-API). A request without B3 ids,
or with ids that are none, starts a new trace, so the calls it causes still
belong together. The agent reports no spans of its own.
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

# b3: {trace id}-{span id}[-{sampling}[-{parent span id}]]; a bare sampling
# state ("0", "d") names no trace.
_B3_SINGLE = re.compile(r"([0-9a-fA-F]{16}|[0-9a-fA-F]{32})-([0-9a-fA-F]{16})(-.*)?")

_B3_TRACE_ID = re.compile(r"[0-9a-fA-F]{16}|[0-9a-fA-F]{32}")
_B3_SPAN_ID = re.compile(r"[0-9a-fA-F]{16}")
# A `b3` header that only carries the sampling decision, as its own header.
_B3_SAMPLING = {
    "0": ("x-b3-sampled", "0"),
    "1": ("x-b3-sampled", "1"),
    "d": ("x-b3-flags", "1"),
}

_ids: ContextVar[Optional[dict[str, str]]] = ContextVar("trace_ids", default=None)
_trace: ContextVar[Optional[dict[str, str]]] = ContextVar("trace", default=None)


def current_request_id() -> Optional[str]:
    """The id of the request being handled; None outside of one."""
    return (_ids.get() or {}).get("request_id")


def current_trace_ids() -> dict[str, str]:
    """
    The ids every log line of the request carries; empty outside of one.

    trace_id and span_id (B3), request_id, and client_trace_id if the caller
    sent `x-client-trace-id`.
    """
    return dict(_ids.get() or {})


def trace_headers() -> dict[str, str]:
    """The tracing headers to pass on from the request being handled."""
    return dict(_trace.get() or {})


def _b3_ids(trace: dict[str, str]) -> tuple[str, str]:
    """
    Trace and span id of the request; starts a new trace if none came in.

    The new ids are added to `trace`, so they go out with the other headers.
    """
    single = _B3_SINGLE.fullmatch(trace.get("b3", ""))
    if single:
        return single[1], single[2]
    # A `b3` without a trace must not travel next to new x-b3-* ids: receivers
    # prefer it and would lose the trace. Its sampling decision is kept.
    sampling = _B3_SAMPLING.get(trace.pop("b3", ""))
    if sampling:
        trace.setdefault(*sampling)
    # Ids that are not B3 ids start a new trace instead of becoming the request
    # id of the answer (audit 13.11., L-22); a span belongs to its trace.
    if not _B3_TRACE_ID.fullmatch(trace.get("x-b3-traceid", "")):
        for name in ("x-b3-traceid", "x-b3-spanid", "x-b3-parentspanid"):
            trace.pop(name, None)
    elif not _B3_SPAN_ID.fullmatch(trace.get("x-b3-spanid", "")):
        trace.pop("x-b3-spanid", None)
    trace_id = trace.setdefault("x-b3-traceid", uuid.uuid4().hex)
    span_id = trace.setdefault("x-b3-spanid", uuid.uuid4().hex[:16])
    return trace_id, span_id


@contextmanager
def traced(incoming: Mapping[str, str]) -> Iterator[str]:
    """Set ids and tracing headers from a request's headers; yields the request id."""
    headers = {k.lower(): v for k, v in incoming.items()}
    trace = {
        name: headers[name]
        for name in TRACE_HEADERS
        if _TRACE_VALUE.fullmatch(headers.get(name, ""))
    }
    trace_id, span_id = _b3_ids(trace)
    # An x-request-id that is too long to be the id of answer and log (65-128
    # characters) is not passed on either: the agent's services would log an
    # id nobody else has (audit 14.11., list S-N). The id below takes its place.
    if not _PLAIN_TOKEN.fullmatch(trace.get("x-request-id", "-")):
        del trace["x-request-id"]
    request_id = next(
        (
            headers[name]
            for name in (CLIENT_TRACE_HEADER, "x-request-id")
            if _PLAIN_TOKEN.fullmatch(headers.get(name, ""))
        ),
        trace_id,
    )
    # Downstream log lines can be matched with the agent's own.
    trace.setdefault("x-request-id", request_id)

    ids = {"trace_id": trace_id, "span_id": span_id, "request_id": request_id}
    if CLIENT_TRACE_HEADER in trace:
        ids["client_trace_id"] = trace[CLIENT_TRACE_HEADER]

    ids_token = _ids.set(ids)
    trace_token = _trace.set(trace)
    try:
        yield request_id
    finally:
        _trace.reset(trace_token)
        _ids.reset(ids_token)


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
