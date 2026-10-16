"""
Which credentials an LLM call to the B-API carries.

Two ways, selected by METADATA_AGENT_B_API_AUTH_MODE (see ENV-PARAMETER.md):

- the agent's own key from B_API_KEY, sent as X-API-KEY - the original way,
  needed for direct calls, tests and anything without an incoming request;
- the caller's key, which the B-API proxy forwards as `Authorization: Bearer
  <key>` when the service is registered with forwardApiKey. The B-API then
  books usage and limits on that key (WLO user or guest key).

The forwarded value is passed on exactly as received and lives only in the
context of the request that brought it: never on a shared object, never in a
log. asyncio tasks copy the context when they are created, so the parallel
field extraction of one request inherits its key while concurrent requests
keep their own.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from .llm_errors import LLMCredentialsMissing

AUTH_MODES = ("static", "forward", "auto")

_forwarded: ContextVar[Optional[str]] = ContextVar(
    "forwarded_b_api_authorization", default=None
)


def bearer_or_none(value: Optional[str]) -> Optional[str]:
    """
    The header value if it is a Bearer credential, else None.

    The value is returned unchanged - the B-API expects it byte for byte and
    answers a lower-case `bearer` with 401 itself, which is a clearer signal
    than anything the agent could invent. Other schemes (e.g. Basic) are no
    B-API key and are ignored.
    """
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return value


@contextmanager
def forwarded_authorization(value: Optional[str]) -> Iterator[None]:
    """Make a forwarded Authorization value visible to LLM calls of this request."""
    token = _forwarded.set(bearer_or_none(value))
    try:
        yield
    finally:
        _forwarded.reset(token)


class ForwardedAuthorizationMiddleware:
    """
    ASGI middleware: capture the incoming Authorization header per request.

    Plain ASGI rather than BaseHTTPMiddleware, so the value is set for the
    whole lifetime of the request in the very task that serves it.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        raw = next(
            (v for k, v in scope.get("headers", []) if k == b"authorization"), None
        )
        value = raw.decode("latin-1") if raw is not None else None
        with forwarded_authorization(value):
            await self.app(scope, receive, send)


def _missing_message(mode: str) -> str:
    if mode == "forward":
        return (
            "Kein Authorization-Header von der B-API erhalten (B_API_AUTH_MODE=forward). "
            "Läuft der Aufruf über /api/v1/proxy/<dienst> und ist dort forwardApiKey gesetzt?"
        )
    if mode == "static":
        return "B_API_KEY ist nicht gesetzt (B_API_AUTH_MODE=static)."
    return (
        "Weder ein durchgereichter Authorization-Header noch B_API_KEY vorhanden "
        "(B_API_AUTH_MODE=auto)."
    )


def b_api_auth(settings) -> tuple[dict[str, str], str]:
    """
    The single auth header for a B-API call, and where it came from.

    Returns ({header: value}, "forwarded" | "static"). Exactly one header:
    a valid Bearer next to an invalid X-API-KEY is a 401 at the B-API.

    Raises:
        LLMCredentialsMissing: the mode allows no source that is available.
    """
    mode = settings.b_api_auth_mode
    forwarded = _forwarded.get()
    if mode != "static" and forwarded:
        return {"Authorization": forwarded}, "forwarded"
    if mode != "forward" and settings.b_api_key:
        return {"X-API-KEY": settings.b_api_key}, "static"
    raise LLMCredentialsMissing(_missing_message(mode))


def current_auth_source(settings) -> Optional[str]:
    """Where the key of a B-API call would come from right now, or None."""
    try:
        return b_api_auth(settings)[1]
    except LLMCredentialsMissing:
        return None


def mask_header(value: Optional[str]) -> dict:
    """Presence, scheme and token length of a header - never the value."""
    if not value:
        return {"present": False}
    scheme, _, token = value.partition(" ")
    if token:
        return {"present": True, "scheme": scheme, "length": len(token)}
    return {"present": True, "scheme": None, "length": len(value)}
