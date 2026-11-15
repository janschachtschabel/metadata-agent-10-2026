"""
The steps of a generation run that need no service (MetadataService.generate_metadata).

Pure functions over field lists and metadata dicts: which fields to ask the LLM
for, how its answers merge with what the record already holds, and in which
order the result is returned.
"""

import copy
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Collection, Optional

from ..config import MAX_WORKERS
from ..utils.schema_loader import get_ai_fillable_fields, get_schema_fields

logger = logging.getLogger(__name__)


@dataclass
class GenerationOptions:
    """What one run of MetadataService.generate_metadata extracts, and how."""

    context: str = "default"
    version: str = "latest"
    # A schema file, or 'auto' for detection.
    schema_file: str = "auto"
    # Existing metadata to update.
    existing_metadata: Optional[dict[str, Any]] = None
    language: str = "de"
    # Parallel worker count.
    max_workers: int = MAX_WORKERS
    # Include core fields (title, description, etc.).
    include_core: bool = True
    # Convert location addresses to coordinates.
    enable_geocoding: bool = True
    # Apply normalization to extracted values.
    normalize_output: bool = True
    # Normalize vocabulary values using fuzzy matching.
    normalize_vocabularies: bool = True
    # Field IDs to regenerate (re-extract).
    regenerate_fields: Optional[list[str]] = None
    # Re-extract fields that are empty in existing_metadata.
    regenerate_empty: bool = False
    # Existing field origins (field_id -> 'ai'|'user').
    origins: Optional[dict[str, str]] = None
    # Loop time the whole run must end by (the /generate deadline); geocoding
    # gets only what is left of it.
    deadline: Optional[float] = None


UPDATE_MARKER_WARNING = "Text-basierte Metadaten-Übergabe erkannt: [IST-STAND]/[UPDATE]"

# [IST-STAND] <json> [UPDATE] <text>, and the English spellings: the marker
# the text starts with, and the one that ends the JSON. Searched as literals -
# one pattern for the whole shape tried every split of a run of blanks between
# its quantifiers and held the event loop for minutes (audit 13.11., P-08).
_UPDATE_MARKERS = [
    (start, re.compile(re.escape(end), re.IGNORECASE))
    for start, end in (
        ("[IST-STAND]", "[UPDATE]"),
        ("[CURRENT]", "[UPDATE]"),
        ("[EXISTING]", "[NEW]"),
    )
]
# Header keys of an exported record; not metadata to carry over.
_RECORD_HEADER_KEYS = (
    "contextName",
    "schemaVersion",
    "metadataset",
    "language",
    "exportedAt",
    "processing",
)

# Complex object fields: their empty value is what the web component exports.
_EMPTY_COMPLEX_VALUES: dict[str, list[dict]] = {
    "schema:eventSchedule": [{}],
    "schema:openingHoursSpecification": [{}],
    "schema:location": [{}],
    "schema:organizer": [],
    "schema:actor": [],
    "schema:performer": [],
    "schema:attendee": [],
    "schema:offers": [{}],
    "schema:accessService": [],
    "schema:subEvent": [],
    "schema:recordedIn": [],
}


def generation_status(fields_extracted: int, fields_failed: int) -> str:
    """
    success: no technical failure. partial: some fields failed, others came
    back. failed: failures and not a single value - nothing worth applying.
    """
    if fields_failed == 0:
        return "success"
    return "partial" if fields_extracted > 0 else "failed"


def parse_update_text(text: str) -> tuple[str, Optional[dict[str, Any]]]:
    """
    Split text with [IST-STAND]/[UPDATE] (or [CURRENT]/[UPDATE], [EXISTING]/[NEW]).

    Returns (update text, existing metadata from the JSON before the marker),
    or (text, None) when there is no marker or the part before it is no JSON object.
    """
    stripped = text.strip()
    for start, end in _UPDATE_MARKERS:
        if stripped[: len(start)].upper() != start:
            continue
        found = end.search(stripped, len(start))
        if not found:
            continue
        try:
            existing_metadata = json.loads(stripped[len(start) : found.start()])
        except (json.JSONDecodeError, RecursionError):
            # Thousands of nested brackets are no record either; uncaught,
            # the RecursionError was a 500 (audit 14.11., L-38).
            continue
        if isinstance(existing_metadata, dict):
            for key in _RECORD_HEADER_KEYS:
                existing_metadata.pop(key, None)
            return (stripped[found.end() :].strip(), existing_metadata)

    return (text, None)


def apply_update_markers(
    text: str, existing_metadata: Optional[dict[str, Any]]
) -> tuple[str, Optional[dict[str, Any]], bool]:
    """
    Text and existing metadata after [IST-STAND]/[UPDATE] markers.

    Metadata from the markers wins over existing_metadata. The third value says
    whether markers were found. An empty record between them ("{}") is one:
    the text is split, and existing_metadata stays what it was - taken as "no
    markers", the markers went to the model as text (audit 14.11., L-38).
    """
    update_text, parsed = parse_update_text(text)
    if parsed is None:
        return text, existing_metadata, False
    if not parsed:
        return update_text, existing_metadata, True
    merged = {**existing_metadata, **parsed} if existing_metadata else parsed
    return update_text, merged, True


def get_default_value_for_field(field: dict[str, Any]) -> Any:
    """The empty value of a field as the web component exports it."""
    field_id = field.get("id", "")
    if field_id in _EMPTY_COMPLEX_VALUES:
        # A fresh copy: the result goes into a response that may be edited.
        return copy.deepcopy(_EMPTY_COMPLEX_VALUES[field_id])

    system = field.get("system", {})
    datatype = system.get("datatype", "string")
    if datatype == "array" or system.get("multiple", False):
        return []
    if datatype == "object":
        return {}
    if datatype in ("number", "integer", "boolean"):
        return None
    return ""  # string, date, datetime, time, url, etc.


def load_extraction_fields(
    context: str, version: str, schema_file: str, include_core: bool, warnings: list
) -> list[dict]:
    """
    The AI-fillable fields: core first (when wanted), then the content type's.

    A failing content-type schema raises; a failing core schema is a warning.
    A field both declare is the content type's: asked for twice, the later
    answer won (audit 14.11., L-35).
    """
    fields = get_ai_fillable_fields(context, version, schema_file)
    core_fields = []
    if include_core and schema_file != "core.json":
        try:
            core_fields = get_ai_fillable_fields(context, version, "core.json")
        except Exception as e:
            warnings.append(f"Could not load core fields: {str(e)}")
    own_ids = {field.get("id") for field in fields}
    return [f for f in core_fields if f.get("id") not in own_ids] + fields


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == []


def select_fields_to_extract(
    all_fields: list[dict],
    existing_metadata: Optional[dict[str, Any]],
    regenerate_fields: Optional[list[str]],
    regenerate_empty: bool,
    origins: Optional[dict[str, str]] = None,
) -> list[dict]:
    """
    All fields, or with a regeneration option: the listed ones, the empty ones
    (regenerate_empty) and those the record does not have yet.

    A field the editor filled (origin "user") is left out unless listed in
    regenerate_fields: merge_extracted keeps an unlisted user value, so the
    call would be paid for, thrown away and counted as extracted. A listed
    one gets the new value (merge_extracted, origin 'ai').
    """
    regenerate_fields = regenerate_fields or []
    user_filled = {
        field_id
        for field_id, origin in (origins or {}).items()
        if origin == "user"
        and existing_metadata
        and not is_effectively_empty(existing_metadata.get(field_id))
    }
    if not (regenerate_fields or regenerate_empty):
        return [f for f in all_fields if f.get("id", "") not in user_filled]

    selected = []
    for field in all_fields:
        field_id = field.get("id", "")
        if field_id in regenerate_fields:
            selected.append(field)
        elif field_id in user_filled:
            continue
        elif (
            regenerate_empty
            and existing_metadata
            and _is_blank(existing_metadata.get(field_id))
        ):
            selected.append(field)
        elif not (existing_metadata and field_id in existing_metadata):
            selected.append(field)
    return selected


def is_effectively_empty(value: Any) -> bool:
    """[], "", {}, None and lists of nothing but such entries ([None], [""], [{}])."""
    if value is None or value == [] or value == "" or value == {}:
        return True
    if isinstance(value, list):
        return all(item is None or item == "" or item == {} for item in value)
    return False


def merge_extracted(
    existing_metadata: Optional[dict[str, Any]],
    extracted_values: dict[str, Any],
    origins: Optional[dict[str, str]],
    regenerate_fields: Optional[list[str]] = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """
    The record after a run, and the origin ('ai'/'user') of each changed field.

    An extracted value replaces the existing one and is marked 'ai' - except
    where the editor entered the existing value (origin 'user') and did not
    ask for it again in regenerate_fields, and where it would replace a value
    with an effectively empty one (a correction round must not lose values).
    """
    flat_metadata = {
        key: value
        for key, value in (existing_metadata or {}).items()
        if not key.startswith("_")
    }
    field_origins = dict(origins) if origins else {}
    asked_again = set(regenerate_fields or [])

    skipped_empty, kept_user = [], []
    for field_id, value in extracted_values.items():
        if value is None:
            continue
        existing_val = flat_metadata.get(field_id)
        # What the editor entered stays, and stays theirs: re-extraction and a
        # content-type switch send it with origin "user".
        if (
            field_origins.get(field_id) == "user"
            and field_id not in asked_again
            and not is_effectively_empty(existing_val)
        ):
            kept_user.append(field_id)
            continue
        if is_effectively_empty(value) and not is_effectively_empty(existing_val):
            skipped_empty.append(field_id)
            continue
        flat_metadata[field_id] = value
        field_origins[field_id] = "ai"

    if kept_user:
        logger.info(f"🛡️ RE-EXTRACTION: kept {len(kept_user)} user values: {kept_user}")
    if skipped_empty:
        logger.info(
            f"🛡️ RE-EXTRACTION: protected {len(skipped_empty)} fields from empty overwrite: {skipped_empty}"
        )
    return flat_metadata, field_origins


def log_re_extraction(extracted_values: dict[str, Any]) -> None:
    """Trace what a re-extraction got back: how many, and which fields first."""
    extracted_keys = [k for k, v in extracted_values.items() if v is not None]
    null_count = sum(1 for v in extracted_values.values() if v is None)
    logger.info(
        f"🔄 RE-EXTRACTION: LLM extracted {len(extracted_keys)} non-null values, {null_count} null values"
    )
    # Field ids only: the values are content, and content stays out of the log.
    if extracted_keys:
        logger.info(f"  📝 {', '.join(extracted_keys[:5])}")


def _schema_fields_or_none(context: str, version: str, schema_file: str) -> list[dict]:
    try:
        return get_schema_fields(context, version, schema_file)
    except Exception as e:
        logger.warning(
            f"⚠️ Fields of {schema_file} ({context}/{version}) not loaded: {e}"
        )
        return []


def order_by_schema(
    flat_metadata: dict[str, Any],
    context: str,
    version: str,
    schema_file: str,
    include_core: bool,
) -> dict[str, Any]:
    """
    Every schema field in schema order (core first), empty ones with their
    default value, then whatever else the record holds - the web component's
    output format.
    """
    schema_fields = []
    if include_core and schema_file != "core.json":
        schema_fields += _schema_fields_or_none(context, version, "core.json")
    schema_fields += _schema_fields_or_none(context, version, schema_file)

    ordered = {}
    for field in schema_fields:
        field_id = field.get("id", "")
        ordered[field_id] = (
            flat_metadata[field_id]
            if field_id in flat_metadata
            else get_default_value_for_field(field)
        )
    for field_id, value in flat_metadata.items():
        ordered.setdefault(field_id, value)
    return ordered


def final_origins(
    ordered_metadata: dict[str, Any],
    field_origins: dict[str, str],
    extracted_values: dict[str, Any],
    asked: Collection[str] = (),
) -> dict[str, str]:
    """
    An origin for every returned field. Without a recorded one: 'ai' for a
    field the model answered, and for one it was asked for (`asked`) that is
    empty - nobody entered that; in a first run every empty field came back
    as the editor's (audit 14.11., L-N). Else 'user': a value the caller
    brought and the model did not replace.
    """

    def origin(field_id: str, value: Any) -> str:
        if field_id in field_origins:
            return field_origins[field_id]
        if field_id in extracted_values:
            return "ai"
        if field_id in asked and is_effectively_empty(value):
            return "ai"
        return "user"

    return {
        field_id: origin(field_id, value)
        for field_id, value in ordered_metadata.items()
    }


def run_outcome(
    extraction: dict[str, Any], extracted_values: dict[str, Any]
) -> dict[str, Any]:
    """
    Counts and status of a run from LLMService.extract_fields_parallel's result.

    Only technical failures count as failed - a field the text simply has no
    value for is not one. A fatal error leaves the remaining fields unrequested.
    Returns {"status", "fields_extracted", "fields_failed", "error"}; error is
    the dict to report, or None.
    """
    fatal_error = extraction.get("fatal_error")
    # [] or [None] is no value: counting it made a run of only empty answers
    # and failures "partial" instead of "failed" (final audit 18.10.).
    fields_extracted = len(
        [v for v in extracted_values.values() if not is_effectively_empty(v)]
    )
    fields_failed = (
        len(extraction.get("errors", []))
        + extraction.get("skipped", 0)
        + len(extraction.get("timed_out", []))
    )
    if fatal_error is not None:
        fields_failed += 1
    status = generation_status(fields_extracted, fields_failed)

    error = None
    cause = fatal_error or extraction.get("first_error")
    if status != "success" and cause is not None:
        error = cause.to_dict()
    elif status == "failed":
        error = {
            "code": "EXTRACTION_FAILED",
            "message": f"Kein Feld konnte generiert werden ({fields_failed} Fehler).",
        }
    return {
        "status": status,
        "fields_extracted": fields_extracted,
        "fields_failed": fields_failed,
        "error": error,
    }
