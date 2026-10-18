"""Error responses as the API reports them (detail + error with code and request id)."""

from typing import cast

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..services.input_source_service import TextExtractionError
from ..utils.request_id import current_request_id
from ..services.llm_errors import LLMError, http_status_for
import logging

logger = logging.getLogger(__name__)


# HTTP status per refusal reason from RepositoryService.save_node. Everything
# here is a statement about the node itself, not about the request — the body
# would be accepted unchanged for a different node.
SAVE_NODE_ERROR_STATUS = {
    "node_too_old": 403,
    "node_age_unknown": 403,
    "node_not_found": 404,
    # Not a statement about the node: the repository could not be read.
    "node_unreadable": 502,
}


def _one_line(text: object) -> str:
    """Text for a log line: a CR or LF from the request must not start a forged one."""
    return str(text).replace("\r", " ").replace("\n", " ")


def failure_response(
    status_code: int, detail: str, error: dict, **extra
) -> JSONResponse:
    """
    An error as the API reports it: a readable `detail`, as FastAPI clients
    expect it, and `error` with the code and the request id for programs and
    support. One log line under the same id; the technical cause stays there.
    """
    error = {**error, "request_id": current_request_id()}
    context = ", ".join(
        f"{key}={error[key]}"
        for key in ("upstream_status", "auth_source")
        if error.get(key)
    )
    message = _one_line(error.get("message") or detail)
    logger.error(
        f"❌ [{error['request_id']}] {status_code} {error.get('code')}: {message}"
        + (f" ({context})" if context else "")
    )
    return JSONResponse(
        status_code=status_code, content={"detail": detail, "error": error, **extra}
    )


# Code per HTTP status for errors raised as HTTPException (audit API-03).
ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "REQUEST_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_ERROR",
    503: "SERVICE_UNAVAILABLE",
    504: "TIMEOUT",
}


async def http_exception_handler(request: Request, exc: Exception) -> Response:
    """
    HTTPException in the API's error shape: `detail` as before, plus `error`.

    Client errors are logged at info level - they are the caller's, not ours.
    """
    exc = cast(StarletteHTTPException, exc)  # registered for this type only
    status = exc.status_code
    message = exc.detail if isinstance(exc.detail, str) else ERROR_CODES.get(status)
    error = {
        "code": ERROR_CODES.get(status, f"HTTP_{status}"),
        "message": message or f"HTTP {status}",
        "request_id": current_request_id(),
    }
    log = logger.error if status >= 500 else logger.info
    # The detail can echo request input: one line, as in failure_response.
    logged = _one_line(error["message"])
    log(f"[{error['request_id']}] {status} {error['code']}: {logged}")
    return JSONResponse(
        status_code=status,
        content={"detail": exc.detail, "error": error},
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(request: Request, exc: Exception) -> Response:
    """Request validation (422): the list of problems under `detail`, plus `error`."""
    exc = cast(RequestValidationError, exc)  # registered for this type only
    error = {
        "code": "VALIDATION_ERROR",
        "message": "Der Request entspricht nicht dem Schema.",
        "request_id": current_request_id(),
    }
    logger.info(f"[{error['request_id']}] 422 VALIDATION_ERROR")
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors()), "error": error},
    )


def generation_timeout_response(seconds: float) -> JSONResponse:
    """An AI run passed METADATA_AGENT_GENERATE_TIMEOUT; whatever ran is cancelled."""
    return failure_response(
        504,
        f"KI-Metadatengenerierung nach {seconds:g} s abgebrochen "
        "(METADATA_AGENT_GENERATE_TIMEOUT).",
        {"code": "GENERATION_TIMEOUT", "message": "Zeitlimit überschritten"},
    )


def upload_timeout_response(seconds: float, result: dict) -> JSONResponse:
    """
    A write passed METADATA_AGENT_UPLOAD_TIMEOUT (audit D-04).

    `result` is RepositoryService's answer: it says what happened to the node
    (discarded_node, or node to clean up or that stays in the workflow).
    """
    extra = {key: result[key] for key in ("discarded_node", "node") if key in result}
    return failure_response(
        504,
        f"Schreiben ins Repository nach {seconds:g} s abgebrochen "
        f"(METADATA_AGENT_UPLOAD_TIMEOUT): {result.get('error', '')}",
        {"code": "UPLOAD_TIMEOUT", "message": "Zeitlimit überschritten"},
        **extra,
    )


def llm_error_response(error: LLMError, **extra) -> JSONResponse:
    """
    An LLM failure. Upstream auth failures become 502 on purpose - a 401/403
    from the agent would make edu-sharing treat the user's own session as
    expired.
    """
    return failure_response(error.http_status, error.message, error.to_dict(), **extra)


def text_extraction_error_response(error: TextExtractionError) -> JSONResponse:
    """A failed URL text extraction with its named cause (504 timeout, 502 other)."""
    return failure_response(error.http_status, error.message, error.to_dict())


def generation_failed_response(result: dict) -> JSONResponse:
    """A /generate run without a single usable field - never a 200."""
    processing = result.get("processing") or {}
    error = processing.get("error") or {"code": "GENERATION_FAILED", "message": ""}
    return failure_response(
        http_status_for(error.get("code")),
        f"KI-Metadatengenerierung fehlgeschlagen: {error.get('message')}",
        error,
        contextName=result.get("contextName"),
        schemaVersion=result.get("schemaVersion"),
        metadataset=result.get("metadataset"),
        language=result.get("language"),
        processing=processing,
    )


class UnexpectedErrorMiddleware:
    """
    ASGI middleware: an exception no endpoint handled becomes a 500 in the API's
    error shape (audit API-02).

    Registered innermost, so CORS and the request id wrap the answer like any
    other; Starlette's own fallback sits outside all of them and answers plain
    text without either. The traceback goes to the log under the request id,
    never to the client. Once a response has started it cannot be replaced -
    the exception then propagates as before.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as e:
            if started:
                raise
            logger.exception(
                f"❌ [{current_request_id()}] Unhandled {type(e).__name__} "
                f"in {scope.get('method')} {scope.get('path')}"
            )
            response = failure_response(
                500,
                "Interner Fehler. Bitte mit der Request-ID melden.",
                {"code": "INTERNAL_ERROR", "message": "Interner Fehler"},
            )
            await response(scope, receive, send)
