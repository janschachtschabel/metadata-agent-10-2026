"""
Shared dependencies of the routers.

Routers look these up at call time (`deps.settings`, `deps.get_metadata_service(...)`),
so one replacement here - in tests via monkeypatch - reaches every endpoint.
"""

import json
from fastapi import HTTPException

from ..services.repository_curation import is_valid_node_id

from ..config import get_settings
from ..services.diagnostics import check_dependencies
from ..services.input_source_service import get_input_source_service
from ..services.metadata_service import get_metadata_service as _get_metadata_service
from ..services.repository_service import get_repository_service
from ..services.llm_service import get_llm_service as _get_llm_service
from ..services.screenshot_service import (
    get_screenshot_service,
)


# Same instance as get_settings(): read once, shared by app, routers and lifespan.
settings = get_settings()

__all__ = [
    "settings",
    "get_metadata_service",
    "get_repository_service",
    "get_llm_service",
    "get_input_source_service",
    "get_screenshot_service",
    "check_dependencies",
    "carry_schema_markers",
    "sanitize_json_string",
    "check_provider_override",
    "check_model_override",
    "parse_json_object",
    "require_repository_service",
    "require_node_id",
]


def carry_schema_markers(data: dict) -> dict:
    """
    Move contextName, schemaVersion and metadataset into `metadata`.

    /generate answers with those markers *beside* `metadata`, while every
    consumer looks for them *inside* it. Posting that response verbatim — the
    way the docs describe — would otherwise fall back to auto-detection:
    /upload writes only the core.json fields and silently drops the
    type-specific ones, /validate and /export/markdown work against a schema
    they guessed. Markers already present inside win, which is what the web
    component sends.

    Mutates and returns `data`. The markers are filtered out again before
    anything reaches the repository.
    """
    inner = data.get("metadata")
    if not isinstance(inner, dict):
        return data
    for marker in ("contextName", "schemaVersion", "metadataset"):
        if marker in data and marker not in inner:
            inner[marker] = data[marker]
    return data


# Largest body whose invalid JSON is repaired. The repair walks the text in
# Python on the event loop: 20 MB took 2.4 s, for every request on the worker
# (audit 18.10. final, S-16). Pasted multi-line texts are far below this; a
# larger invalid body is refused with 400 like any other invalid JSON. Valid
# JSON of any size up to METADATA_AGENT_MAX_REQUEST_BYTES parses as before.
MAX_REPAIR_CHARS = 1_000_000


def sanitize_json_string(raw_body: str) -> str:
    """
    Sanitize raw JSON string by escaping control characters in string values.
    This allows malformed JSON with literal newlines in strings to be parsed.
    Handles multi-line text input that wasn't properly escaped.

    Bodies over MAX_REPAIR_CHARS come back unrepaired.
    """
    # Remove BOM if present
    raw_body = raw_body.lstrip("\ufeff")

    # First, try to parse as-is
    try:
        json.loads(raw_body)
        return raw_body  # Already valid JSON
    except json.JSONDecodeError:
        pass
    if len(raw_body) > MAX_REPAIR_CHARS:
        return raw_body
    return _repair_control_characters(raw_body)


def _repair_control_characters(raw_body: str) -> str:
    """Escape literal newlines and tabs inside JSON strings, drop other controls."""
    # This handles cases where users paste multi-line text without escaping
    result = []
    in_string = False
    escape_next = False

    for char in raw_body:
        if escape_next:
            result.append(char)
            escape_next = False
            continue

        if char == "\\":
            result.append(char)
            escape_next = True
            continue

        if char == '"':
            in_string = not in_string
            result.append(char)
            continue

        if in_string:
            # Escape control characters inside strings
            if char == "\n":
                result.append("\\n")
            elif char == "\r":
                result.append("\\r")
            elif char == "\t":
                result.append("\\t")
            elif ord(char) < 32:
                # Remove other control characters
                pass
            else:
                result.append(char)
        else:
            result.append(char)

    return "".join(result)


def check_provider_override(provider: str | None) -> None:
    """
    Refuse a request for native OpenAI on a service that runs on the B-API.

    Native OpenAI is billed to the server's OPENAI_API_KEY and receives the
    caller's text directly; on a B-API deployment that would also bypass
    B_API_AUTH_MODE=forward. It stays available where it is what the operator
    configured.
    """
    if provider == "openai" and settings.llm_provider != "openai":
        raise HTTPException(
            status_code=422,
            detail=(
                "llm_provider 'openai' ist nur erlaubt, wenn der Dienst selbst mit "
                f"Provider openai läuft (hier: {settings.llm_provider})."
            ),
        )


def check_model_override(provider: str | None, model: str | None) -> None:
    """
    Refuse a model outside METADATA_AGENT_ALLOWED_LLM_MODELS (empty: any).

    A model named per request runs on the server's key wherever no caller key
    is forwarded (audit 18.10. final, S-19). The provider's configured default
    is always allowed; the answer names no setting.
    """
    allowed = settings.allowed_llm_model_names()
    if not allowed or model is None:
        return
    default = settings.get_llm_config(provider_override=provider)["model"]
    if model not in allowed and model != default:
        raise HTTPException(
            status_code=422,
            detail=f"llm_model '{model}' ist auf diesem Dienst nicht freigegeben.",
        )


def get_metadata_service(llm_provider: str | None = None, llm_model: str | None = None):
    """The metadata service - for an allowed model only (check_model_override)."""
    check_model_override(llm_provider, llm_model)
    return _get_metadata_service(llm_provider=llm_provider, llm_model=llm_model)


def get_llm_service(llm_provider: str | None = None, llm_model: str | None = None):
    """The LLM service - for an allowed model only (check_model_override)."""
    check_model_override(llm_provider, llm_model)
    return _get_llm_service(llm_provider=llm_provider, llm_model=llm_model)


def parse_json_object(raw_body: bytes, example: str) -> dict:
    """
    Parse a request body that has to be a JSON object, or answer 400.

    Invalid UTF-8, invalid JSON and a valid JSON value of the wrong kind (a
    list, a number) are all the caller's mistake. Left to the endpoint they
    surfaced as 500s from `"metadata" in data` or `data.pop(...)`.
    """
    try:
        data = json.loads(sanitize_json_string(raw_body.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Ungültiger JSON-Body: {e}") from e
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=400, detail=f"Body muss ein JSON-Objekt sein, z.B. {example}."
        )
    return data


def require_repository_service():
    """The repository service, or a 503 when its credentials are not configured."""
    repo_service = get_repository_service()
    if not repo_service:
        raise HTTPException(
            status_code=503,
            detail="Repository service not configured. Set WLO_GUEST_USERNAME and WLO_GUEST_PASSWORD environment variables.",
        )
    return repo_service


def require_node_id(node_id: str) -> None:
    """
    Reject a path parameter that is not an edu-sharing node id.

    The value is interpolated into the repository URL the service builds, and
    that request carries the service account's credentials — an id containing
    '/' would steer it at a different endpoint.
    """
    if not is_valid_node_id(node_id):
        raise HTTPException(
            status_code=400,
            detail="Ungültige node_id — erwartet wird eine Node-UUID, z.B. '3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9'.",
        )
