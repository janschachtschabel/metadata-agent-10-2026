"""
What the request and response models share: boundary validators, limits, the
schema markers inside `metadata`, the error envelope.
"""

import re
from typing import Annotated, Any, ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..utils.loggable import shortened
from .repository_rules import extract_id_from_url, is_valid_node_id


def blank_is_unset(value: Any) -> Any:
    """
    Read a field left empty as 'not given' rather than as an empty value.

    The examples in the Swagger UI are form-shaped: every option spelled out,
    the unused ones left empty. What arrives is `""`, and any consumer asking
    `is not None` then takes 'left blank' for a deliberate choice. That is how
    the documented upload example wrote an empty comment into every node's
    workflow history instead of the default it promises — answered `200`, with
    nothing to notice.

    Applied at the boundary, so `is not None` keeps its meaning further in for
    a caller who genuinely means to send an empty value.

    Optional text fields only. A field with a non-empty default (`context`,
    `version`) must keep its `""`, because None would fail its own type check
    before the endpoint's `or "default"` ever runs.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


def validate_node_id(value: Any) -> Any:
    """
    Reject anything that is present but is not an edu-sharing node id.

    Shared by every request model carrying one — the value reaches a repository
    URL that is called with the service account's credentials, so the shape has
    to be checked at the boundary rather than at each call site.

    A blank string means 'nothing entered' and becomes None. Whether the field is
    required at all depends on `input_source`, and each branch of the endpoint
    checks its own with a message that says so — this validator cannot know, and
    answering a text-input request with 'Ungültige node_id' names a field that
    request does not use.
    """
    if isinstance(value, str) and not value.strip():
        return None
    if value is None or is_valid_node_id(value):
        return value
    raise ValueError(
        "Ungültige node_id — erwartet wird eine Node-UUID, "
        "z.B. '3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9'."
    )


# llm_model goes on unchanged into the B-API body, error messages and logs;
# model and route names fit this, blanks and control characters do not.
LLM_MODEL_PATTERN = r"^[A-Za-z0-9._:/@-]+$"


LLM_MODEL_MAX_LENGTH = 200


# What a request names goes into error answers and log lines: bounded here,
# where 1 MB of `context` came back as a 2 MB answer (audit 14.11., S-34).
MAX_CONTEXT_LENGTH = 64


MAX_VERSION_LENGTH = 32


MAX_SCHEMA_FILE_LENGTH = 300  # a file name or the URI of a content type


MAX_FIELD_ID_LENGTH = 200


MAX_LANGUAGE_LENGTH = 8


# One collection (id or pasted link) or workflow state of a list.
ListedName = Annotated[str, Field(max_length=200)]


# No URL in use is longer. Without a bound, megabytes went into the screenshot
# browser and from its error message into the log (audit 14.11., S-31).
MAX_URL_LENGTH = 8192


def normalize_provider(value: Any) -> Any:
    """Read a provider name like the setting does: case and spaces do not matter."""
    if isinstance(value, str):
        return value.strip().lower() or None
    return value


def normalize_collection_ids(v: Any) -> Any:
    """
    Drop empty entries and refuse anything that does not name a collection.

    A list that holds nothing usable counts as absent. Each entry may be a bare
    id or a pasted collection link, but what it resolves to becomes part of an
    authenticated repository URL — so it has to be a node id.
    """
    if not isinstance(v, list):
        # Anything else — including a bare ID — is left for the type check
        # to reject, so the caller gets told what shape was expected.
        return v
    cleaned = [str(x).strip() for x in v if x is not None and str(x).strip()]
    invalid = [x for x in cleaned if not is_valid_node_id(extract_id_from_url(x))]
    if invalid:
        raise ValueError(
            f"Ungültige collection_id {shortened(invalid[0])!r} — erwartet wird eine "
            "Node-UUID oder ein Sammlungs-Link, der eine enthält."
        )
    return cleaned or None


# How much of a text sanitize_text cleans: four times what the API layer keeps
# (MAX_TEXT_LENGTH in api/input_resolver.py), so that the kept part is clean
# even where cleaning removed a lot. The passes over a whole body of 19 MB
# took 406 ms on the event loop, for text that is cut off (audit 14.11., L-41).
SANITIZED_LENGTH = 600_000


def sanitize_text(text: str) -> str:
    """
    Sanitize text input by normalizing control characters.
    - Preserves newlines (\n), tabs (\t), and carriage returns (\r)
    - Removes other control characters (0x00-0x1F except \t, \n, \r)
    - Normalizes various whitespace characters to regular spaces

    Only the first SANITIZED_LENGTH characters are cleaned. What follows stays
    as received: the API layer cuts the text far below that, and its warning
    about the cut still names the length of the whole text.
    """
    if not text:
        return text

    rest = text[SANITIZED_LENGTH:]
    text = text[:SANITIZED_LENGTH]

    # Remove NULL bytes and other problematic control characters
    # Keep: \t (0x09), \n (0x0A), \r (0x0D)
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)

    # Normalize various Unicode whitespace to regular spaces
    text = re.sub(r"[\u00A0\u2000-\u200B\u202F\u205F\u3000]", " ", text)

    # Normalize Windows line endings to Unix
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Remove excessive whitespace (more than 2 consecutive newlines)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return (text + rest).strip()


class LocalizedString(BaseModel):
    """Localized string with de/en support."""

    de: Optional[str] = None
    en: Optional[str] = None


def check_schema_markers(
    metadata: dict[str, Any], markers: tuple[str, ...]
) -> dict[str, Any]:
    """
    The schema markers inside `metadata` are text (or null), `_schema` an object.

    {"schemaVersion": 2} ended in a 500 and a contextName list in a 200 with
    "unhashable type" as its message (audit API-07); a refusal here is a 422.
    """
    for marker in markers:
        _check_marker(marker, metadata.get(marker), _MARKER_LENGTHS[marker])
    legacy = metadata.get("_schema")
    if legacy is not None:
        if not isinstance(legacy, dict):
            raise ValueError("_schema muss ein Objekt sein")
        for key, limit in _LEGACY_MARKER_LENGTHS.items():
            _check_marker(f"_schema.{key}", legacy.get(key), limit)
    return metadata


# The markers are the same values as the options context, version, schema_file
# and language, and bounded like them.
_MARKER_LENGTHS = {
    "contextName": MAX_CONTEXT_LENGTH,
    "schemaVersion": MAX_VERSION_LENGTH,
    "metadataset": MAX_SCHEMA_FILE_LENGTH,
    "language": MAX_LANGUAGE_LENGTH,
}


_LEGACY_MARKER_LENGTHS = {
    "context": MAX_CONTEXT_LENGTH,
    "version": MAX_VERSION_LENGTH,
    "file": MAX_SCHEMA_FILE_LENGTH,
}


def _check_marker(name: str, value: Any, limit: int) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"{name} muss ein Text sein")
    if len(value) > limit:
        raise ValueError(f"{name} darf höchstens {limit} Zeichen lang sein")


_SCHEMA_MARKERS = ("contextName", "schemaVersion", "metadataset")


class SchemaMarkersMixin(BaseModel):
    """
    For request models that carry a metadata record: its schema markers are
    checked when the request is read (check_schema_markers).

    Before anything is written: afterwards it was a 500 for a node that
    existed (audit 13.11., API-13). One validator instead of one per model
    (audit 14.11., Q-07); it adds no field, the JSON schemas stay as they were.
    """

    # /export/markdown reads `language` from the record as well.
    schema_markers: ClassVar[tuple[str, ...]] = _SCHEMA_MARKERS

    @field_validator("metadata", "expected_metadata", check_fields=False)
    @classmethod
    def _markers_are_text(
        cls, value: Optional[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        # None: /upload/verify without a record to compare with.
        return (
            value if value is None else check_schema_markers(value, cls.schema_markers)
        )


class ErrorInfo(BaseModel):
    """Machine-readable part of an error answer."""

    model_config = ConfigDict(extra="allow")

    code: str = Field(
        ...,
        description="Fehlercode, z. B. NOT_FOUND, LLM_AUTH_REJECTED, UPLOAD_TIMEOUT",
    )
    message: str = Field(..., description="Kurze Beschreibung")
    request_id: Optional[str] = Field(
        None,
        description="ID der Anfrage, auch im Header X-Request-ID; im Log des Dienstes zu finden",
    )


class ErrorResponse(BaseModel):
    """Every error answer of the API: `detail` for people, `error` for programs."""

    detail: Any = Field(
        ..., description="Lesbare Meldung; bei 422 die Liste der Schema-Verstöße"
    )
    error: ErrorInfo
    # Only on 504 UPLOAD_TIMEOUT: what happened to the node (audit D-04).
    node: Optional[dict[str, Any]] = Field(
        None,
        description="Nur bei 504 UPLOAD_TIMEOUT: der Node, der erhalten blieb "
        "(im Workflow, vollständig geschrieben, nicht verwerfbar oder vom Aufrufer "
        "übergeben) — mit nodeId",
    )
    discarded_node: Optional[str] = Field(
        None,
        description="Nur bei 504 UPLOAD_TIMEOUT: ID des dabei angelegten und "
        "wieder verworfenen Nodes",
    )
    collections: Optional[list[dict[str, Any]]] = Field(
        None,
        description="Nur bei 504 UPLOAD_TIMEOUT während des Verknüpfens der "
        "Sammlungen: je Sammlung, ob der Node (in `node`) verknüpft wurde",
    )
