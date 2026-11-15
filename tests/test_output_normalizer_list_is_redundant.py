"""
Proof that dropping the hard-coded list in `OutputNormalizer` changes nothing.

`normalize_output` carried six field names — `schema:actor`, `schema:performer`,
`schema:attendee`, `schema:organizer`, `schema:contributor`, `schema:author` —
and lifted strings into `{"name": ...}` for exactly those. `FieldNormalizer` now
does the same thing for every field the schema declares as an object, and runs
first in the pipeline (`metadata_service.generate`: field normalization, then
geocoding, then output normalization).

Removing the list is only safe if the two produce identical results for those six
fields. That is what this pins down. It stays in the suite afterwards: it is the
test that fails if someone changes the object key convention and quietly alters
the output format for every event record.

Audit 14.11.2026, Q-16: the conversion of the old list (`_normalize_person_array`)
was dead code in OutputNormalizer since then. It lives here now, as the
reference the FieldNormalizer is compared with.
"""

import copy

import json
from pathlib import Path

import pytest

from src.services.field_normalizer import FieldNormalizer
from src.services.output_normalizer import OutputNormalizer

SCHEMATA = Path(__file__).resolve().parents[1] / "src" / "schemata" / "mds_oeh"

FORMER_LIST = [
    "schema:actor",
    "schema:performer",
    "schema:attendee",
    "schema:organizer",
    "schema:contributor",
    "schema:author",
]

VALUES = [
    "TEEKAYART",
    ["Andrea Welz", "Max Mustermann"],
    [{"name": "Schon korrekt", "url": "https://example.org"}],
    [{"@value": "Aus einer LLM-Antwort"}],
]


def old_person_list(value):
    """What OutputNormalizer made of a value of the six listed fields."""
    if not value:
        return []
    if not isinstance(value, list):
        value = [value]
    result = []
    for item in value:
        if isinstance(item, str):
            result.append({"name": item})
        elif isinstance(item, dict):
            if "name" not in item and "@value" in item:
                item["name"] = item.pop("@value")
            result.append(item)
    return result


def declarations():
    """
    Every declaration of a field of the old list: each schema of each version,
    so the check is not event-only and not 2.0.0-only.

    Built from the schemas: the fields no schema declares (schema:contributor
    and schema:author in no version, schema:actor only in 1.8.0) skipped on
    every run when the list was the six names against 2.0.0 (audit 14.11.,
    Q-14). A field that returns to a schema is compared again by itself.
    """
    found = []
    for path in sorted(SCHEMATA.glob("v*/*.json")):
        if path.name == "manifest.json":
            continue
        schema = json.loads(path.read_text(encoding="utf-8"))
        for field in schema.get("fields") or []:
            if field.get("id") in FORMER_LIST:
                place = f"{path.parent.name}/{path.stem}/{field['id']}"
                found.append(pytest.param(field["system"], id=place))
    return found


DECLARATIONS = declarations()


@pytest.mark.parametrize("system", DECLARATIONS)
def test_the_field_normalizer_matches_the_old_person_list(system):
    new = FieldNormalizer()

    for value in VALUES:
        # Copies: the old conversion renamed "@value" in place.
        expected = old_person_list(copy.deepcopy(value))
        actual = new.normalize_field_value(copy.deepcopy(value), system)
        assert actual == expected, (
            f"with {value!r}: FieldNormalizer gave {actual!r}, "
            f"the old list gave {expected!r}"
        )


def test_the_comparison_reaches_the_fields_the_current_schema_declares():
    """A wrong path would leave nothing to compare - and nothing to fail."""
    compared = {declaration.id for declaration in DECLARATIONS}

    for field_id in ("schema:performer", "schema:attendee", "schema:organizer"):
        assert f"v2.0.0/event/{field_id}" in compared


def test_the_special_cases_stay_in_the_output_normalizer():
    """Opening hours and event schedules carry semantics no generic branch has."""
    normalizer = OutputNormalizer()
    result = normalizer.normalize_output(
        {
            "schema:openingHoursSpecification": [
                {"dayOfWeek": "Montag", "opens": "09:00"}
            ],
        }
    )
    assert result["schema:openingHoursSpecification"][0]["dayOfWeek"] == ["MO"]


def test_the_hard_coded_list_is_gone():
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "services"
        / "output_normalizer.py"
    ).read_text(encoding="utf-8")
    assert "person_fields" not in source, (
        "The name list is back. Object shape is derived from the schema in "
        "FieldNormalizer._is_object_field — a list covered 4 of 37 object fields "
        "and is how twelve records got a string into schema:provider."
    )
