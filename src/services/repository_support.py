"""Repository configuration and the small helpers the write paths share (names, results, problems)."""

import base64
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..config import get_settings

logger = logging.getLogger(__name__)


def build_auth_header(username: str, password: str) -> Optional[str]:
    """
    Build a Basic Auth header for the WLO service account.

    Returns None when either credential is missing, so callers can fall back to
    anonymous access instead of sending a header for empty credentials.
    """
    if not username or not password:
        return None
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {encoded}"


def repository_root(repository_url: str) -> str:
    """
    The configured repository URL without '/rest' and without a trailing slash.

    The one place that reads METADATA_AGENT_REPOSITORY_URL's form: the setting
    is documented with '/rest', and one without it wrote nodes but answered
    every read of one with 404 (audit 14.11., R-12).
    """
    base = repository_url.rstrip("/")
    if base.endswith("/rest"):
        base = base[: -len("/rest")]
    return base


def rest_base(repository_url: str) -> str:
    """The base of the repository's REST API: '<root>/rest'."""
    return f"{repository_root(repository_url)}/rest"


def get_repository_config() -> dict:
    """Build repository config from settings (single configured URL)."""
    settings = get_settings()

    # Without '/rest': the upload endpoints append '/rest/...' themselves.
    return {
        "base_url": repository_root(settings.repository_url),
        "inbox_id": settings.wlo_inbox_id,
    }


def first_value(value: Any) -> Any:
    """Unwrap a repository property: they are arrays, the answer wants one value."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def text_value(value: Any) -> Optional[str]:
    """
    first_value as text, for the answer's title and description.

    The body is not type-checked field by field: a number there is written as it
    came, and the answer must not fail on it once the node exists.
    """
    value = first_value(value)
    return None if value is None else str(value)


def duplicate_skipped(warning: str) -> str:
    """
    Message for a duplicate check that could not run.

    The upload goes ahead — a flaky search must not block every upload — but
    the caller has to know that nobody looked for an existing copy.
    """
    return f"Dublettenprüfung nicht möglich ({warning}) — Node ohne Prüfung angelegt"


def nothing_written(metadata_result: dict) -> bool:
    """True when the repository took none of the fields it was sent."""
    return metadata_result.get("fields_written", 0) == 0 and not metadata_result.get(
        "success", True
    )


def partial_problems(
    metadata_result: dict,
    aspect_problem: Optional[str],
    extended_result: Optional[dict],
    collection_result: Optional[dict],
    workflow_result: Optional[dict],
    preview_result: Optional[dict] = None,
) -> list[str]:
    """
    Name every step that failed although the node itself was written.

    Each of them leaves the node short of what was sent — a missing collection
    reference, a skipped review state, coordinates the repository dropped — and
    the caller can only act on it if the answer says so.
    """
    problems = []
    field_errors = metadata_result.get("field_errors") or []
    if field_errors:
        problems.append(
            f"{len(field_errors)} Feld(er) konnten nicht geschrieben werden"
        )
    if aspect_problem:
        problems.append(aspect_problem)
    if extended_result is not None and not extended_result.get("success"):
        failed = extended_result.get("failed_fields")
        problems.append(
            f"Erweiterte Daten nicht geschrieben: {', '.join(failed)}"
            if failed
            else "Erweiterte Daten (ccm:oeh_extendedType/Data/Text) konnten "
            "nicht geschrieben werden"
        )
    if extended_result is not None and extended_result.get("note"):
        problems.append(extended_result["note"])
    failed_collections = [
        str(entry["collectionId"])
        for entry in (collection_result or {}).get("results", [])
        if not entry["success"]
    ]
    if failed_collections:
        problems.append(
            f"Sammlung(en) nicht verknüpft: {', '.join(failed_collections)}"
        )
    failed_steps = [
        step["status"]
        for step in (workflow_result or {}).get("steps", [])
        if not step["success"]
    ]
    if failed_steps:
        problems.append(f"Workflow-Schritt(e) nicht gesetzt: {', '.join(failed_steps)}")
    if preview_result is not None and not preview_result["success"]:
        problems.append(f"Preview: {preview_result['error']}")
    return problems


def identifying_url(metadata: dict) -> Optional[str]:
    """
    The URL a node is identified by, as one string.

    _create_node writes schema:url into ccm:wwwurl for schemas without the
    latter, so the duplicate check has to look at the same fallback — and the
    search API takes a list of strings, not a list holding a list.
    """
    # As text: the body is not type-checked field by field, and a number here
    # failed the answer model after the node was written (audit 13.11., API-13).
    return text_value(metadata.get("ccm:wwwurl") or metadata.get("schema:url")) or None


def node_created_at(node: dict[str, Any]) -> Optional[datetime]:
    """
    Read the creation time of a repository node as an aware UTC datetime.

    edu-sharing reports it twice: `createdAt` beside the node as ISO 8601, and
    `cm:created` among the properties as epoch milliseconds. Both are read
    because the second survives a `propertyFilter` that drops the first.

    Returns None when neither is present or parsable — a node whose age cannot
    be established, which callers guarding on age must not treat as young.
    """
    return _node_time(node, "createdAt", "cm:created")


def node_modified_at(node: dict[str, Any]) -> Optional[datetime]:
    """The last modification, like node_created_at (`modifiedAt`, `cm:modified`)."""
    return _node_time(node, "modifiedAt", "cm:modified")


def _node_time(node: dict[str, Any], key: str, prop: str) -> Optional[datetime]:
    stamp = node.get(key)
    if isinstance(stamp, str) and stamp.strip():
        try:
            parsed = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
            return (
                parsed
                if parsed.tzinfo is not None
                else parsed.replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass

    millis = first_value((node.get("properties") or {}).get(prop))
    try:
        return datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def repository_error(response: Any) -> str:
    """
    'HTTP <status>: <message>' for a refused repository request.

    edu-sharing answers with JSON holding a short `message` beside the Java
    exception class and a stack trace. Only the message reaches the client;
    the full body goes to the log (audit S-06). Without JSON: the status alone.
    """
    logger.warning(
        f"Repository answered HTTP {response.status_code}: {response.text[:500]}"
    )
    message = ""
    try:
        data = response.json()
        if isinstance(data, dict):
            message = str(data.get("message") or "")
    except Exception:
        message = ""
    message = " ".join(message.split())[:200]
    return (
        f"HTTP {response.status_code}: {message}"
        if message
        else (f"HTTP {response.status_code}")
    )


# Envelope keys of a /generate answer or an upload body - no metadata fields.
# Together with the rule for a leading "_" (_origins, _source_text, ...) the one
# definition of what reaches the repository (audit L-05).
ENVELOPE_KEYS = frozenset(
    {
        "contextName",
        "schemaVersion",
        "metadataset",
        "metadataset_uri",
        "language",
        "exportedAt",
        "processing",
        "preview_image_url",
    }
)


SCHEMA_MARKERS = ("contextName", "schemaVersion", "metadataset")

# The options a flat /upload body carries beside the fields, and those of
# /nodes: no metadata either. The routers lift them out by name; a comparison
# with what was sent has to leave out the same ones (audit 14.11., R-08).
UPLOAD_OPTION_KEYS = (
    "node_id",
    "repository",
    "check_duplicates",
    "start_workflow",
    "source",
    "preview_url",
    "screenshot_method",
    "write_extended_data",
    "extended_text",
    "return_full_node",
    "collection_id",
    "workflow_steps",
    "workflow_comment",
    "workflow_receiver",
)
SAVE_NODE_OPTION_KEYS = (
    "node_id",
    "collection_id",
    "write_extended_data",
    "extended_text",
    "preview",
    "start_review_workflow",
    "start_quality_workflow",
)


def is_metadata_key(key: str) -> bool:
    return key not in ENVELOPE_KEYS and not key.startswith("_")


def metadata_fields(metadata: dict) -> dict:
    """The metadata fields of a flat body or of one nested under 'metadata'."""
    inner = metadata.get("metadata")
    source = inner if isinstance(inner, dict) else metadata
    return {k: v for k, v in source.items() if is_metadata_key(k)}


def sent_record(body: dict) -> dict:
    """
    A body as the write path reads it: the fields flat, the schema markers with them.

    /upload takes the fields flat, under `metadata` or under `metadata.metadata`
    (web component); markers beside `metadata` move in unless it has its own,
    as the routers do it. /upload/verify compared the nested forms as if they
    had no fields (audit 14.11., R-07).
    """
    record = body
    for _ in range(2):
        inner = record.get("metadata")
        if not isinstance(inner, dict):
            break
        markers = {m: record[m] for m in SCHEMA_MARKERS if m in record}
        record = {**markers, **inner}
    return record
