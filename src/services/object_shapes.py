"""
Which fields hold objects, and whether a record's values have that shape.

The schema decides, never the value. Functions without the LLM-output
normalizer: the repository write path only checks (find_object_shape_violations)
and must not depend on what repairs model answers (audit 14.11., Q-07).
"""

import logging
from typing import Any

from ..utils.schema_loader import load_schema

logger = logging.getLogger(__name__)


def is_object_field(field_schema: dict) -> bool:
    """Does the SCHEMA say this field holds objects?

    All four spellings count. The schemas were unified on
    `datatype: array` + `items.datatype: object`, but older releases still
    carry `datatype: object` with `multiple: true` and the variants at the
    system level, and both are read here.
    """
    items = field_schema.get("items") or {}
    return (
        field_schema.get("datatype") == "object"
        or items.get("datatype") == "object"
        or bool(items.get("variants"))
        or bool(field_schema.get("variants"))
    )


def object_key(field_schema: dict) -> str:
    """The sub-field a bare string belongs in.

    `name` when the variant declares one — that is the schema.org convention
    and what the old person list of OutputNormalizer produced. Otherwise the first
    declared sub-field, which for `ContactPoint` is `contactType`, matching
    how the organization records in the repository already use it.
    """
    items = field_schema.get("items") or {}
    for variant in items.get("variants") or field_schema.get("variants") or []:
        names = [f.get("id") for f in (variant.get("fields") or []) if f.get("id")]
        if "name" in names:
            return "name"
        if names:
            return names[0]
    return "name"


def find_object_shape_violations(
    metadata: dict[str, Any], context: str, version: str, schema_file: str
) -> list[dict[str, Any]]:
    """Name the fields whose value does not have the shape the schema declares.

    Reports, it does NOT repair. An earlier version lifted the value into shape
    here, which meant the repository received something the editor had never
    seen on screen — a metadata tool must write what was reviewed and approved,
    not a corrected version of it. Repair belongs where the result is still
    visible and can be rejected: the web component does it at the input, and
    FieldNormalizer does it to the LLM output before the canvas shows it.

    At this point the only honest options are to write the value as it is, which
    poisons the Elasticsearch mapping, or to refuse the write and say which
    field is wrong. This function provides the second.

    Never raises. A schema that cannot be loaded yields no findings — refusing a
    write because a schema file is missing would be worse than writing a value
    whose shape nobody could check.
    """
    try:
        schema = load_schema(context, version, schema_file)
        fields = list(schema.get("fields") or [])
        if schema_file != "core.json":
            try:
                core = load_schema(context, version, "core.json")
                known = {f.get("id") for f in fields}
                fields += [
                    f for f in (core.get("fields") or []) if f.get("id") not in known
                ]
            except Exception as e:
                logger.warning(
                    f"⚠️ Shape check without core.json ({context}/{version}): {e}"
                )
    except Exception as e:
        # No schema, no shape to check against; the write path decides on its own.
        logger.warning(f"⚠️ Shape check skipped, {schema_file} not loaded: {e}")
        return []

    violations: list[dict[str, Any]] = []

    for field in fields:
        field_id = field.get("id")
        system = field.get("system") or {}
        if field_id not in metadata or not is_object_field(system):
            continue

        value = metadata[field_id]
        if value is None or value == "" or value == []:
            continue

        items = value if isinstance(value, list) else [value]
        if all(isinstance(item, dict) for item in items):
            continue

        key = object_key(system)
        violations.append(
            {
                "field_id": field_id,
                "value": value,
                "expected_key": key,
                "message": (
                    f"{field_id}: erwartet ein Objekt, bekommen {value!r}. "
                    f'Richtig wäre z.B. {{"{key}": "..."}}.'
                ),
            }
        )

    return violations
