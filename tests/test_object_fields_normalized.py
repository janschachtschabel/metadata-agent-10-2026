"""
A bare string in an object-typed field must never reach the repository.

`OutputNormalizer` used to lift strings into objects for six hard-coded field
names — all of them person roles from the event schemas. The schemas declare 37
object-typed fields, so `person.json` (14 object fields) and `organization.json`
(11) had no coverage at all. That is where the damage happened: twelve
`education_offer` records carry `schema:provider` as a plain string, one person
carries `schema:contactPoint: ["Virtuelles Haus"]`.

The fix belongs in `FieldNormalizer`, which already dispatches on the schema's
`datatype`. It just had no branch for objects, so a string fell through to
`# Default: return as-is`. These tests pin the branch down for every object field
the schemas declare, not for a list of names — a list is what failed last time.

Only OBJECT elements are normalized here. Scalar list elements are deliberately
left alone: `_normalize_date` and `_normalize_number` return None for anything
they cannot parse, so applying them per element would silently drop values that
are wrong but recoverable.
"""

import json
from pathlib import Path

import pytest

from src.services.field_normalizer import FieldNormalizer

SCHEMATA = Path(__file__).resolve().parents[1] / "src" / "schemata"
CONTEXT_VERSION = SCHEMATA / "mds_oeh" / "v2.0.0"


def object_fields():
    """Every object-typed top-level field of the active metadata set."""
    found = {}
    for path in sorted(CONTEXT_VERSION.glob("*.json")):
        if path.name == "manifest.json":
            continue
        schema = json.loads(path.read_text(encoding="utf-8"))
        for field in schema.get("fields") or []:
            system = field.get("system") or {}
            items = system.get("items") or {}
            is_object = (
                system.get("datatype") == "object"
                or items.get("datatype") == "object"
                or bool(items.get("variants"))
                or bool(system.get("variants"))
            )
            if is_object:
                found.setdefault(f"{path.stem}:{field['id']}", system)
    return found


OBJECT_FIELDS = object_fields()


def test_the_schemas_still_declare_object_fields():
    """Guards the parametrization below: an empty list would make it vacuous."""
    assert len(OBJECT_FIELDS) >= 20, f"only {len(OBJECT_FIELDS)} object fields found"


@pytest.mark.parametrize("name", sorted(OBJECT_FIELDS))
def test_a_bare_string_is_lifted_into_an_object(name):
    system = OBJECT_FIELDS[name]
    result = FieldNormalizer().normalize_field_value("Beispielwert", system)

    values = result if isinstance(result, list) else [result]
    assert values, f"{name}: the value disappeared"
    for item in values:
        assert isinstance(item, dict), (
            f"{name}: expected an object, got {type(item).__name__} — this is exactly "
            "the value that fixes the Elasticsearch path to `text`"
        )
    assert "Beispielwert" in json.dumps(values, ensure_ascii=False), (
        f"{name}: the original value was lost during normalization"
    )


def test_the_three_cases_that_actually_broke_staging():
    """Real values from the affected records, with their real schemas."""
    normalizer = FieldNormalizer()
    cases = [
        ("education_offer.json", "schema:provider", "Stuttgarter Musikschule"),
        ("person.json", "schema:contactPoint", ["Virtuelles Haus"]),
        ("event.json", "schema:organizer", ["TEEKAYART"]),
    ]
    for schema_file, field_id, value in cases:
        schema = json.loads((CONTEXT_VERSION / schema_file).read_text(encoding="utf-8"))
        system = next(f["system"] for f in schema["fields"] if f["id"] == field_id)
        result = normalizer.normalize_field_value(value, system)
        items = result if isinstance(result, list) else [result]
        assert all(isinstance(i, dict) for i in items), f"{field_id}: {result!r}"


def test_an_object_that_is_already_correct_is_left_alone():
    schema = json.loads(
        (CONTEXT_VERSION / "organization.json").read_text(encoding="utf-8")
    )
    system = next(
        f["system"] for f in schema["fields"] if f["id"] == "schema:contactPoint"
    )
    correct = [
        {
            "contactType": "Besucherservice",
            "email": "spectrum@technikmuseum.berlin",
            "telephone": "+49 (0) 30 43 97 34 0",
        }
    ]
    assert FieldNormalizer().normalize_field_value(correct, system) == correct


def test_a_string_field_is_not_wrapped_into_an_object():
    """The branch must key off the schema, not off the value being a string."""
    system = {"datatype": "string", "multiple": False}
    assert (
        FieldNormalizer().normalize_field_value("Kunstmuseum", system) == "Kunstmuseum"
    )


def test_an_empty_value_stays_empty():
    schema = json.loads((CONTEXT_VERSION / "person.json").read_text(encoding="utf-8"))
    system = next(
        f["system"] for f in schema["fields"] if f["id"] == "schema:contactPoint"
    )
    normalizer = FieldNormalizer()
    assert normalizer.normalize_field_value([], system) == []
    assert normalizer.normalize_field_value("", system) == ""


def test_the_name_field_wins_when_the_variant_declares_one():
    """Person-like variants carry `name`; ContactPoint does not and uses its first field."""
    normalizer = FieldNormalizer()
    person = json.loads((CONTEXT_VERSION / "event.json").read_text(encoding="utf-8"))
    organizer = next(
        f["system"] for f in person["fields"] if f["id"] == "schema:organizer"
    )
    assert normalizer.normalize_field_value("TEEKAYART", organizer) == [
        {"name": "TEEKAYART"}
    ]

    contact = json.loads((CONTEXT_VERSION / "person.json").read_text(encoding="utf-8"))
    system = next(
        f["system"] for f in contact["fields"] if f["id"] == "schema:contactPoint"
    )
    assert normalizer.normalize_field_value("Virtuelles Haus", system) == [
        {"contactType": "Virtuelles Haus"}
    ]
