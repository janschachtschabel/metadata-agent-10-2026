"""
Every list field must say what it is a list of.

This is not tidiness. `ccm:oeh_extendedData` is stored as one JSON blob and
indexed by Elasticsearch with dynamic mapping, so the FIRST record that fills a
path decides its type for the whole index. A field that the schema declares as
an object but that carries a bare string fixes the path to `text`, and from then
on every correct record is rejected with `mapper_parsing_exception` and never
enters the index at all. That happened on staging in August 2026 and took the
collection sync down with it.

Both normalizers and both validators (backend and web component) are supposed to
derive their behaviour from the schema. They can only do that if the schema is
unambiguous, which means:

1. The element type of every list is derivable — either from `items.datatype` or,
   for `multiple` scalars, from `datatype` itself.
2. Object lists use one style: `datatype: "array"` + `items.datatype: "object"`.
   The older `datatype: "object"` + `multiple: true` put `variants` at the system
   level, where `llm_service._build_object_structure` and the web component's
   ShapeExpanderService — both of which read `items.variants` — silently found
   nothing. Person fields therefore had no object form in the editor at all.
3. No field path carries contradictory type classes across schemas. Every schema
   writes into the same index path, so a field that is an object in one content
   type and a string in another is the exact failure above waiting to happen.
"""

import json
from collections import defaultdict
from pathlib import Path

import pytest

SCHEMATA = Path(__file__).resolve().parents[1] / "src" / "schemata"

SCALARS = {
    "string",
    "uri",
    "url",
    "text",
    "markdown",
    "date",
    "datetime",
    "time",
    "integer",
    "number",
    "boolean",
    "duration",
    "json",
    "vocab",
}


def schema_files():
    return sorted(p for p in SCHEMATA.glob("*/v*/*.json") if p.name != "manifest.json")


def walk(fields, prefix=""):
    """Yield (path, system) for every field, including those inside variants."""
    for field in fields or []:
        system = field.get("system") or {}
        path = system.get("path") or field.get("id")
        if not path:
            continue
        full = prefix + path
        yield full, system
        for variant in system.get("variants") or []:
            yield from walk(variant.get("fields"), full + ".")
        for variant in (system.get("items") or {}).get("variants") or []:
            yield from walk(variant.get("fields"), full + ".")


def type_class(system):
    """objekt / skalar / None if the declaration does not say."""
    datatype = system.get("datatype")
    items = system.get("items") or {}
    if datatype == "array":
        if items.get("datatype") == "object" or items.get("variants"):
            return "objekt"
        if items.get("datatype") in SCALARS:
            return "skalar"
        return None
    if datatype == "object" or system.get("variants"):
        return "objekt"
    if datatype in SCALARS:
        return "skalar"
    return None


@pytest.mark.parametrize(
    "path",
    schema_files(),
    ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}/{p.name}",
)
def test_every_list_says_what_it_holds(path):
    schema = json.loads(path.read_text(encoding="utf-8"))
    undeclared = [
        name
        for name, system in walk(schema.get("fields"))
        if system.get("datatype") == "array" and type_class(system) is None
    ]
    assert not undeclared, (
        f"{path.name}: these fields are declared as an array without saying what "
        f"the elements are, so no schema-driven check can validate them: {undeclared}"
    )


@pytest.mark.parametrize(
    "path",
    schema_files(),
    ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}/{p.name}",
)
def test_object_lists_use_the_items_style(path):
    """`datatype: object` + `multiple: true` hides the variants from every reader."""
    schema = json.loads(path.read_text(encoding="utf-8"))
    old_style = [
        name
        for name, system in walk(schema.get("fields"))
        if system.get("datatype") == "object" and system.get("multiple")
    ]
    assert not old_style, (
        f"{path.name}: {old_style} use `datatype: object` + `multiple: true`. "
        "Write object lists as `datatype: array` with `items.datatype: object` "
        "and the variants inside `items`, otherwise the LLM prompt builder and "
        "the web component's shape expander do not see them."
    )


@pytest.mark.parametrize(
    "path",
    schema_files(),
    ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}/{p.name}",
)
def test_the_version_field_matches_the_directory(path):
    schema = json.loads(path.read_text(encoding="utf-8"))
    expected = path.parent.name.lstrip("v")
    assert schema.get("version") == expected, (
        f"{path}: carries version {schema.get('version')!r} but lives in {path.parent.name}. "
        "Records store this value in `schemaVersion`, and a wrong one sends every "
        "later validation against the wrong schema."
    )


def test_no_field_path_is_object_in_one_schema_and_scalar_in_another():
    seen = defaultdict(lambda: defaultdict(list))
    for path in schema_files():
        schema = json.loads(path.read_text(encoding="utf-8"))
        where = f"{path.parent.parent.name}/{path.parent.name}/{path.name}"
        for name, system in walk(schema.get("fields")):
            klass = type_class(system)
            if klass:
                seen[name][klass].append(where)

    conflicts = {
        name: {k: v[:3] for k, v in classes.items()}
        for name, classes in seen.items()
        if len(classes) > 1
    }
    assert not conflicts, (
        "These field paths are an object in one schema and a scalar in another. "
        "All schemas write into the same Elasticsearch path, so whichever record "
        "lands first decides the type and the other kind can never be indexed:\n"
        + json.dumps(conflicts, indent=2, ensure_ascii=False)
    )


def test_the_stray_schema_copy_is_gone():
    """`v1.8.1 - Kopie` was tracked in git and picked up by every `*/v*/*.json` glob."""
    strays = [p.name for p in SCHEMATA.glob("*/*") if p.is_dir() and "Kopie" in p.name]
    assert not strays, f"leftover schema directories: {strays}"
