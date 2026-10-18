"""Metadata as readable Markdown (/export/markdown, MetadataService.export_to_markdown)."""

import logging
from typing import Any, Optional

from ..utils.schema_loader import load_schema, schema_file_of

logger = logging.getLogger(__name__)


def export_to_markdown(
    metadata: dict[str, Any],
    context: str = "default",
    version: str = "latest",
    schema_file: str = "auto",
    language: str = "de",
    include_empty: bool = False,
) -> str:
    """
    Core fields first, then the content type's, one section per group.

    Groups and fields without a value are left out unless `include_empty`.
    """
    if schema_file == "auto":
        schema_file = schema_file_of(metadata)

    core_schema = _try_load(context, version, "core.json")
    schema = (
        _try_load(context, version, schema_file) if schema_file != "core.json" else None
    )
    if not core_schema and not schema:
        return f"# Metadata\n\nSchema not found: {schema_file}"

    groups, fields = _groups_and_fields(core_schema, schema)
    fields_by_group: dict[str, list] = {}
    for field in fields:
        fields_by_group.setdefault(field.get("group", "other"), []).append(field)

    schema_label = localized(schema.get("label", {}), language) if schema else None
    lines = [f"# {schema_label or schema_file}", ""]
    for group in groups:
        lines.extend(
            _group_section(group, fields_by_group, metadata, language, include_empty)
        )
    return "\n".join(lines)


def _try_load(context: str, version: str, schema_file: str):
    try:
        return load_schema(context, version, schema_file)
    except Exception as e:
        # The export then lists the values without the schema's labels.
        logger.warning(f"⚠️ Export without {schema_file} ({context}/{version}): {e}")
        return None


def _groups_and_fields(core_schema, schema) -> tuple[list, list]:
    """Groups of both schemas without duplicates (core first), and all their fields."""
    groups, fields, seen = [], [], set()
    for source in (core_schema, schema):
        if not source:
            continue
        for group in source.get("groups", []):
            if group.get("id") not in seen:
                groups.append(group)
                seen.add(group.get("id"))
        fields.extend(source.get("fields", []))
    return groups, fields


def _group_section(
    group: dict,
    fields_by_group: dict,
    metadata: dict,
    language: str,
    include_empty: bool,
) -> list[str]:
    group_id = group.get("id", "")
    group_fields = fields_by_group.get(group_id, [])
    if not group_fields:
        return []
    if not include_empty and not any(
        has_meaningful_value(metadata.get(f.get("id"))) for f in group_fields
    ):
        return []

    lines = [f"## {localized(group.get('label', {}), language) or group_id}", ""]
    for field in group_fields:
        value = metadata.get(field.get("id", ""))
        if not has_meaningful_value(value) and not include_empty:
            continue
        label = localized(field.get("label", {}), language)
        lines.append(f"**{label}:** {format_value(value, field, language)}")
    lines.append("")
    return lines


def has_meaningful_value(value: Any) -> bool:
    """Not None, '', [] or a list/object of nothing but empty entries."""
    if value is None or value == "" or value == []:
        return False
    if isinstance(value, list):
        return any(
            item and (not isinstance(item, dict) or any(v for v in item.values()))
            for item in value
        )
    if isinstance(value, dict):
        return any(v for v in value.values())
    return True


def localized(obj: Any, language: str) -> str:
    """The text in `language`, else German, else English, else ''."""
    if isinstance(obj, str):
        return obj
    return obj.get(language, obj.get("de", obj.get("en", "")))


def _empty_marker(language: str) -> str:
    return "_leer_" if language == "de" else "_empty_"


def _concept_labels(field: dict) -> dict:
    """Concepts by what a value holds: their 'uri', or their 'value' (languages, licences)."""
    vocabulary = field.get("system", {}).get("vocabulary", {})
    if not vocabulary:
        return {}
    return {
        c.get("uri") or c.get("value"): c
        for c in vocabulary.get("concepts", [])
        if c.get("uri") or c.get("value")
    }


def _concept_label(value: Any, concepts: dict, language: str) -> Optional[str]:
    """The concept's label for a URI/value; None for anything else, objects included."""
    if not isinstance(value, (str, int, float)) or value not in concepts:
        return None
    return localized(concepts[value].get("label", {}), language) or str(value)


def format_value(value: Any, field: dict[str, Any], language: str) -> str:
    """A value as Markdown text: vocabulary labels, Ja/Nein, lists joined by commas."""
    if value is None or value == []:
        return _empty_marker(language)

    concepts = _concept_labels(field)

    if isinstance(value, list):
        return ", ".join(
            _concept_label(v, concepts, language) or format_single_value(v)
            for v in value
        )

    if isinstance(value, bool):
        if language == "de":
            return "Ja" if value else "Nein"
        return "Yes" if value else "No"

    return _concept_label(value, concepts, language) or format_single_value(value)


def format_single_value(value: Any) -> str:
    """One value as text; from an object its name, label, uri, address or first string."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("name", "label", "uri", "@value"):
            if key in value:
                return str(value[key])
        parts = [
            str(value[key])
            for key in ("streetAddress", "addressLocality", "postalCode")
            if key in value
        ]
        if parts:
            return ", ".join(parts)
        for v in value.values():
            if isinstance(v, str) and v:
                return v
    return str(value)
