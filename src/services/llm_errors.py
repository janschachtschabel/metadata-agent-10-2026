"""
Typed failures of LLM calls.

Callers need to tell a rejected key from a bad answer: a 403 for one field means
a 403 for every field, so extraction stops at the first one instead of sending
dozens of calls that are refused the same way. And no caller may turn such a
failure into a confident result.

`fatal` marks failures that every further call with the same credentials and
model would repeat. `http_status` is what the agent answers its own caller:
502, and 503 while rate limited. Never 401 or 403, not even for a missing key:
the answer travels back through edu-sharing, which would take it for an
expired session of the user.
"""

from __future__ import annotations

from typing import Any, Optional

AUTH_SOURCE_LABELS = {
    "forwarded": "durchgereichter Schlüssel des Aufrufers",
    "static": "eigener B_API_KEY des Agents",
    "openai": "OPENAI_API_KEY",
}


class LLMError(Exception):
    """An LLM call that did not produce an answer."""

    code = "LLM_ERROR"
    http_status = 502
    fatal = False

    def __init__(
        self,
        message: str,
        *,
        upstream_status: Optional[int] = None,
        auth_source: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.upstream_status = upstream_status
        self.auth_source = auth_source

    def to_dict(self) -> dict:
        """What the API reports - never a key, never an upstream stack trace."""
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.upstream_status is not None:
            data["upstream_status"] = self.upstream_status
        if self.auth_source:
            data["auth_source"] = self.auth_source
        return data


class LLMCredentialsMissing(LLMError):
    """No key to call the B-API with: neither forwarded nor configured."""

    code = "LLM_AUTH_MISSING"
    fatal = True


class LLMAuthRejected(LLMError):
    """The B-API does not accept the key (401)."""

    code = "LLM_AUTH_REJECTED"
    fatal = True


class LLMAuthForbidden(LLMError):
    """The key is valid but lacks the right for this call (403)."""

    code = "LLM_AUTH_FORBIDDEN"
    fatal = True


class LLMModelNotFound(LLMError):
    """The configured model does not exist at the provider (404)."""

    code = "LLM_MODEL_NOT_FOUND"
    fatal = True


class LLMRateLimited(LLMError):
    """Still throttled (429) after the retries - the key's quota is exhausted."""

    code = "LLM_RATE_LIMITED"
    http_status = 503
    fatal = True


class LLMUnreachable(LLMError):
    """No connection to the gateway after the retries."""

    code = "LLM_UNREACHABLE"
    fatal = True


class LLMRouteNotFound(LLMError):
    """The B-API router knows no active route of this name (400)."""

    code = "LLM_ROUTE_NOT_FOUND"
    fatal = True


class LLMRouteMisconfigured(LLMError):
    """
    The router cannot serve the request as configured, and will not by retrying:
    a model without price, a model that cannot do the endpoint, or parameters
    the route's models reject. The router reports some of these as 503.
    """

    code = "LLM_ROUTE_MISCONFIGURED"
    fatal = True


class LLMEmptyAnswer(LLMError):
    """
    200, but no text: typically a thinking model that spent the token budget on
    reasoning (finish_reason "length"). Not "no value in the text" - a failure.
    """

    code = "LLM_EMPTY_ANSWER"


class LLMUpstreamError(LLMError):
    """A server error (5xx) that persisted through the retries."""

    code = "LLM_UPSTREAM_ERROR"


class LLMRequestRejected(LLMError):
    """Any other 4xx: this request was refused, others may still pass."""

    code = "LLM_REQUEST_REJECTED"


def http_status_for(code: Optional[str]) -> int:
    """The HTTP status the agent answers for an error code (502 if unknown)."""
    pending = list(LLMError.__subclasses__())
    while pending:
        cls = pending.pop()
        if cls.code == code:
            return cls.http_status
        pending.extend(cls.__subclasses__())
    return LLMError.http_status


def short_upstream_detail(body: str, limit: int = 200) -> str:
    """
    The useful part of an upstream error body, without stack traces.

    The B-API answers 403 with a full Java stack trace in `trace`; only the
    short fields are worth passing on.
    """
    import json

    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        lines = (body or "").strip().splitlines()
        return lines[0][:limit] if lines else ""
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            error = error.get("message") or error.get("code")
        parts = [str(p) for p in (error, data.get("message")) if p]
        return " – ".join(dict.fromkeys(parts))[:limit]
    return str(data)[:limit]
