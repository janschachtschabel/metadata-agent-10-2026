"""
Checking metadata against its schema (/validate, MetadataService.validate_metadata).

Errors make a record invalid: a required field is empty, an object field holds
a scalar, a coordinate is out of range. Everything else is a warning with a
suggested correction where one can be derived (dates, times, URLs, vocabulary).
"""

import re
from typing import Any, Callable, Optional

from ..utils.dates_numbers import ImpossibleDate, read_date, read_datetime, read_time
from ..utils.schema_loader import load_schema, schema_file_of
from ..utils.text_utils import levenshtein_within
from ..utils.vocabulary import concept_keys, is_closed_vocabulary


def validate_metadata(
    metadata: dict[str, Any],
    field_normalizer,
    context: str = "default",
    version: str = "latest",
    schema_file: str = "auto",
) -> dict[str, Any]:
    """
    Validate metadata against its schema plus core.json.

    `field_normalizer` decides which fields hold objects (FieldNormalizer).
    Returns {"valid", "schema_used", "errors", "warnings", "coverage"}; a schema
    that cannot be loaded is reported as an error of field `_schema`.
    """
    if schema_file == "auto":
        schema_file = schema_file_of(metadata)

    try:
        fields = _fields_to_validate(context, version, schema_file)
    except Exception as e:
        return {
            "valid": False,
            "schema_used": schema_file,
            "errors": [{"field_id": "_schema", "message": str(e), "severity": "error"}],
            "warnings": [],
            "coverage": 0.0,
        }

    errors: list[dict] = []
    warnings: list[dict] = []
    required_fields = []
    filled_required = 0

    for field in fields:
        field_id = field.get("id", "")
        system = field.get("system", {})
        value = metadata.get(field_id)

        if system.get("required", False):
            required_fields.append(field_id)
            if _is_empty(value):
                errors.append(_issue(field_id, "Required field is empty", "error"))
            else:
                filled_required += 1

        if _is_empty(value):
            continue
        values = value if isinstance(value, list) else [value]

        # Object fields: the schema decides the shape, and a scalar here is not
        # a cosmetic problem. Elasticsearch maps the path from the first record
        # that fills it, so one string turns the path into `text` and every
        # correctly structured record afterwards is rejected outright and never
        # enters the index. That is an error, not a warning - and this branch is
        # what makes /validate able to find such records.
        if field_normalizer._is_object_field(system):
            errors.extend(_object_errors(field_id, system, values, field_normalizer))
            continue

        warnings.extend(
            _format_warnings(field_id, system.get("datatype", "string"), values)
        )
        errors.extend(_coordinate_errors(field_id, values))
        warnings.extend(_vocabulary_warnings(field_id, system, values))

    coverage = (
        (filled_required / len(required_fields) * 100) if required_fields else 100.0
    )
    return {
        "valid": len(errors) == 0,
        "schema_used": schema_file,
        "errors": errors,
        "warnings": warnings,
        "coverage": round(coverage, 1),
    }


def _fields_to_validate(context: str, version: str, schema_file: str) -> list[dict]:
    """The schema's fields plus the core fields it does not declare itself."""
    schema = load_schema(context, version, schema_file)
    fields = list(schema.get("fields", []))
    # Required core fields (e.g. cclom:title) are validated too.
    if schema_file != "core.json":
        try:
            core_schema = load_schema(context, version, "core.json")
        except Exception:
            return fields  # core schema not available - validate without it
        own_ids = {f.get("id") for f in fields}
        fields.extend(
            f for f in core_schema.get("fields", []) if f.get("id") not in own_ids
        )
    return fields


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _issue(field_id: str, message: str, severity: str) -> dict[str, str]:
    return {"field_id": field_id, "message": message, "severity": severity}


def _object_errors(
    field_id: str, system: dict, values: list, field_normalizer
) -> list[dict]:
    key = field_normalizer._object_key(system)
    return [
        _issue(
            field_id,
            f"Expected an object, got {type(v).__name__}: '{v}'. "
            f'Write it as an object, e.g. {{"{key}": "{v}"}}',
            "error",
        )
        for v in values
        if not isinstance(v, dict)
    ]


# ------------------------------------------------------------------ formats


def _number_problem(v: Any) -> Optional[str]:
    if not isinstance(v, (int, float)):
        return f"Expected number, got {type(v).__name__}: '{v}'"
    return None


def _boolean_problem(v: Any) -> Optional[str]:
    if not isinstance(v, bool):
        return f"Expected boolean, got {type(v).__name__}: '{v}'"
    return None


def _date_problem(v: Any) -> Optional[str]:
    if not isinstance(v, str):
        return None
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
        suggestion = suggest_date_format(v)
        if suggestion:
            return f"Date '{v}' should be formatted as '{suggestion}'"
        return f"Invalid date format '{v}' - expected YYYY-MM-DD"
    month, day = int(v[5:7]), int(v[8:10])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return f"Invalid date values in '{v}'"
    return None


def _datetime_problem(v: Any) -> Optional[str]:
    if not isinstance(v, str) or re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?", v
    ):
        return None
    suggestion = suggest_datetime_format(v)
    if suggestion:
        return f"Datetime '{v}' should be formatted as '{suggestion}'"
    return f"Invalid datetime format '{v}' - expected YYYY-MM-DDTHH:MM"


def _time_problem(v: Any) -> Optional[str]:
    if not isinstance(v, str) or re.match(r"^\d{2}:\d{2}(:\d{2})?$", v):
        return None
    suggestion = suggest_time_format(v)
    if suggestion:
        return f"Time '{v}' should be formatted as '{suggestion}'"
    return f"Invalid time format '{v}' - expected HH:MM"


def _url_problem(v: Any) -> Optional[str]:
    if not isinstance(v, str) or re.match(r"^https?://", v, re.IGNORECASE):
        return None
    if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z]{2,}", v):
        return f"URL '{v}' missing protocol - should be 'https://{v}'"
    return f"Invalid URL format '{v}' - must start with http:// or https://"


FORMAT_CHECKS: dict[str, Callable[[Any], Optional[str]]] = {
    "number": _number_problem,
    "integer": _number_problem,
    "boolean": _boolean_problem,
    "date": _date_problem,
    "datetime": _datetime_problem,
    "time": _time_problem,
    "uri": _url_problem,
    "url": _url_problem,
}


def _format_warnings(field_id: str, datatype: str, values: list) -> list[dict]:
    check = FORMAT_CHECKS.get(datatype)
    if check is None:
        return []
    return [
        _issue(field_id, problem, "warning") for v in values if (problem := check(v))
    ]


def _coordinate_errors(field_id: str, values: list) -> list[dict]:
    """Coordinate fields are recognised by their id (latitude/lat, longitude/lon/lng)."""
    name = field_id.lower()
    if "latitude" in name or "lat" in name:
        axis, limit = "Latitude", 90
    elif "longitude" in name or "lon" in name or "lng" in name:
        axis, limit = "Longitude", 180
    else:
        return []
    return [
        _issue(field_id, f"{axis} {v} out of range (-{limit} to {limit})", "error")
        for v in values
        if isinstance(v, (int, float)) and (v < -limit or v > limit)
    ]


def _vocabulary_warnings(field_id: str, system: dict, values: list) -> list[dict]:
    vocabulary = system.get("vocabulary", {})
    if not is_closed_vocabulary(vocabulary):
        return []
    concepts = vocabulary.get("concepts", [])
    valid = concept_keys(concepts)
    warnings = []
    for v in values:
        if isinstance(v, (dict, list)):
            # Unhashable, and never a concept: a concept is its URI or value.
            message = f"Value '{v}' is no concept of the closed vocabulary"
            warnings.append(_issue(field_id, message, "warning"))
            continue
        if not v or v in valid:
            continue
        closest = find_closest_vocabulary_match(v, concepts)
        message = (
            f"Value '{v}' not in vocabulary. Did you mean '{closest}'?"
            if closest
            else f"Value '{v}' not in closed vocabulary"
        )
        warnings.append(_issue(field_id, message, "warning"))
    return warnings


# -------------------------------------------------------------- suggestions


def find_closest_vocabulary_match(value: Any, concepts: list) -> Optional[str]:
    """
    The URI of the concept whose URI ends in the value ("460", "discipline/460")
    or whose label is close to it. Only whole trailing path segments count: a
    substring test let "a" or "w3id" match the first URI containing it.
    """
    if not isinstance(value, str) or not value.strip("/"):
        return None

    value_lower = value.lower()
    uri_tail = "/" + value_lower.strip("/")
    best_match = None
    best_distance = float("inf")

    for concept in concepts:
        uri = concept.get("uri", "")
        if uri.lower().rstrip("/").endswith(uri_tail):
            return uri

        label = concept.get("label", {})
        for lang_label in label.values() if isinstance(label, dict) else [label]:
            if isinstance(lang_label, str):
                dist = levenshtein_within(
                    value_lower, lang_label.lower(), int(len(value) * 0.4)
                )
                if dist is not None and dist < best_distance:
                    best_distance = dist
                    best_match = uri

    return best_match


def suggest_date_format(value: str) -> Optional[str]:
    """YYYY-MM-DD for DD.MM.YYYY, DD.MM.YY, '15. März 2025' or DD/MM/YYYY; else None."""
    try:
        return read_date(value)
    except ImpossibleDate:
        return None


def suggest_datetime_format(value: str) -> Optional[str]:
    """YYYY-MM-DDTHH:MM for a recognisable date with optional time; else None."""
    return read_datetime(value)


def suggest_time_format(value: str) -> Optional[str]:
    """HH:MM for H:MM / HH:MM or '14 Uhr 30' / '14 Uhr'; else None."""
    return read_time(value)
