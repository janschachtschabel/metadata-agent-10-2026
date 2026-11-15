"""
Checking metadata against its schema (/validate, MetadataService.validate_metadata).

Errors make a record invalid: a required field is empty, an object field holds
a scalar, a coordinate is out of range. Everything else is a warning with a
suggested correction where one can be derived (dates, times, URLs, vocabulary).
"""

import re
from typing import Any, Callable, Iterable, Iterator, Optional

from ..utils.loggable import shortened
from ..utils.dates_numbers import ImpossibleDate, read_date, read_datetime, read_time
from ..utils.schema_loader import load_schema, schema_file_of
from ..utils.vocabulary import is_closed_vocabulary
from .vocabulary_matching import is_concept, suggest_concept


# Bounds of an answer (audit 14.11., S-30). One issue per list value, each
# quoting its value in full, made 106 MB and a peak of 1 GB out of a 4 MB body.
MAX_ISSUES_PER_FIELD = 20  # per field and check, then one entry with the rest
MAX_ISSUES = 500  # errors per answer, and as many warnings
MAX_QUOTED_VALUE_LENGTH = 200


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
        "errors": _bounded(errors, "error"),
        "warnings": _bounded(warnings, "warning"),
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
    """
    No value: None, [], {}, text of nothing but blanks, or a list of only
    such entries. `[""]` and "   " passed as a filled required field (audit
    14.11., L-44); 0 and False are values.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return all(_is_empty(item) for item in value)
    return isinstance(value, dict) and not value


def _issue(field_id: str, message: str, severity: str) -> dict[str, str]:
    return {"field_id": field_id, "message": message, "severity": severity}


def _quoted(value: Any) -> str:
    """A value as a message quotes it: cut to MAX_QUOTED_VALUE_LENGTH."""
    return shortened(value, MAX_QUOTED_VALUE_LENGTH)


def _listed(
    field_id: str, severity: str, problems: Iterable[Optional[str]], total: int
) -> list[dict]:
    """
    One issue per problem of a field's values, at most MAX_ISSUES_PER_FIELD.

    `problems` yields one entry per value (None: nothing to report) and is not
    read further once the bound is reached; a closing entry then counts the
    values left out.
    """
    issues: list[dict] = []
    for index, problem in enumerate(problems):
        if problem is None:
            continue
        if len(issues) == MAX_ISSUES_PER_FIELD:
            message = (
                f"{total - index} further values not checked: at most "
                f"{MAX_ISSUES_PER_FIELD} problems are listed per field"
            )
            issues.append(_issue(field_id, message, severity))
            break
        issues.append(_issue(field_id, problem, severity))
    return issues


def _bounded(issues: list[dict], severity: str) -> list[dict]:
    """At most MAX_ISSUES entries, then one that counts the rest."""
    if len(issues) <= MAX_ISSUES:
        return issues
    message = (
        f"{len(issues) - MAX_ISSUES} further entries not listed: at most "
        f"{MAX_ISSUES} per answer"
    )
    return issues[:MAX_ISSUES] + [_issue("_limit", message, severity)]


def _object_errors(
    field_id: str, system: dict, values: list, field_normalizer
) -> list[dict]:
    key = field_normalizer._object_key(system)

    def problem(v: Any) -> Optional[str]:
        if isinstance(v, dict):
            return None
        return (
            f"Expected an object, got {type(v).__name__}: '{_quoted(v)}'. "
            f'Write it as an object, e.g. {{"{key}": "{_quoted(v)}"}}'
        )

    return _listed(field_id, "error", (problem(v) for v in values), len(values))


# ------------------------------------------------------------------ formats


def _number_problem(v: Any) -> Optional[str]:
    if not isinstance(v, (int, float)):
        return f"Expected number, got {type(v).__name__}: '{_quoted(v)}'"
    return None


def _boolean_problem(v: Any) -> Optional[str]:
    if not isinstance(v, bool):
        return f"Expected boolean, got {type(v).__name__}: '{_quoted(v)}'"
    return None


def _date_problem(v: Any) -> Optional[str]:
    if not isinstance(v, str):
        return None
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
        suggestion = suggest_date_format(v)
        if suggestion:
            return f"Date '{_quoted(v)}' should be formatted as '{suggestion}'"
        return f"Invalid date format '{_quoted(v)}' - expected YYYY-MM-DD"
    try:
        read_date(v)  # the day must exist: 2026-02-30 does not
    except ImpossibleDate:
        return f"Invalid date values in '{_quoted(v)}'"
    return None


def _datetime_problem(v: Any) -> Optional[str]:
    if not isinstance(v, str):
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?", v):
        # Day and time must exist: 2026-02-30T10:00 and T25:00 do not.
        if read_datetime(v):
            return None
        return f"Invalid datetime values in '{_quoted(v)}'"
    suggestion = suggest_datetime_format(v)
    if suggestion:
        return f"Datetime '{_quoted(v)}' should be formatted as '{suggestion}'"
    return f"Invalid datetime format '{_quoted(v)}' - expected YYYY-MM-DDTHH:MM"


def _time_problem(v: Any) -> Optional[str]:
    if not isinstance(v, str) or re.match(r"^\d{2}:\d{2}(:\d{2})?$", v):
        return None
    suggestion = suggest_time_format(v)
    if suggestion:
        return f"Time '{_quoted(v)}' should be formatted as '{suggestion}'"
    return f"Invalid time format '{_quoted(v)}' - expected HH:MM"


def _url_problem(v: Any) -> Optional[str]:
    if not isinstance(v, str) or re.match(r"^https?://", v, re.IGNORECASE):
        return None
    if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z]{2,}", v):
        return f"URL '{_quoted(v)}' missing protocol - should be 'https://{_quoted(v)}'"
    return f"Invalid URL format '{_quoted(v)}' - must start with http:// or https://"


FORMAT_CHECKS: dict[str, Callable[[Any], Optional[str]]] = {
    "number": _number_problem,
    "integer": _number_problem,
    "boolean": _boolean_problem,
    "date": _date_problem,
    "datetime": _datetime_problem,
    "time": _time_problem,
    "uri": _url_problem,
}


def _format_warnings(field_id: str, datatype: str, values: list) -> list[dict]:
    check = FORMAT_CHECKS.get(datatype)
    if check is None:
        return []
    return _listed(field_id, "warning", (check(v) for v in values), len(values))


def _coordinate_errors(field_id: str, values: list) -> list[dict]:
    """
    Coordinate fields are recognised by the last part of their id being
    latitude/lat or longitude/lon/lng. A substring test made a capacity of 200
    ("physicaLATtendee") an out-of-range latitude.
    """
    name = re.split(r"[:./]", field_id.lower())[-1]
    if name in ("latitude", "lat"):
        axis, limit = "Latitude", 90
    elif name in ("longitude", "lon", "lng"):
        axis, limit = "Longitude", 180
    else:
        return []
    problems = (
        f"{axis} {v} out of range (-{limit} to {limit})"
        if isinstance(v, (int, float)) and (v < -limit or v > limit)
        else None
        for v in values
    )
    return _listed(field_id, "error", problems, len(values))


def _vocabulary_warnings(field_id: str, system: dict, values: list) -> list[dict]:
    problems = _vocabulary_problems(system, values)
    return _listed(field_id, "warning", problems, len(values))


def _vocabulary_problems(system: dict, values: list) -> Iterator[Optional[str]]:
    """Per value what is wrong with it for a closed vocabulary, or None."""
    vocabulary = system.get("vocabulary", {})
    if not is_closed_vocabulary(vocabulary):
        return
    concepts = vocabulary.get("concepts", [])
    searches_left = MAX_SUGGESTIONS_PER_FIELD
    for v in values:
        if isinstance(v, (dict, list)):
            # Never a concept: a concept is its URI or value.
            yield f"Value '{_quoted(v)}' is no concept of the closed vocabulary"
            continue
        # The matcher of /generate decides (audit 14.11., L-34): a number or
        # boolean counts by the text JSON writes for it, 0 and false are
        # checked like any value, and the suggestion is the concept's key -
        # also where that is a `value` and no URI.
        if _is_empty(v) or is_concept(v, concepts):
            yield None
            continue
        closest = None
        if searches_left > 0:
            searches_left -= 1
            closest = suggest_concept(v, concepts)
        shown = _quoted(str(v).lower() if isinstance(v, bool) else v)
        yield (
            f"Value '{shown}' not in vocabulary. Did you mean '{closest}'?"
            if closest is not None
            else f"Value '{shown}' not in closed vocabulary"
        )


# -------------------------------------------------------------- suggestions

# The suggestion search compares a value with every label of every concept; 200
# invalid values against 894 occupational categories blocked the event loop for
# 12.6 s (audit P-07). Values past this count are reported without a suggestion.
MAX_SUGGESTIONS_PER_FIELD = 5


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
