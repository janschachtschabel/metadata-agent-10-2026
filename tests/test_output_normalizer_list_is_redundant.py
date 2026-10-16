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
"""

import json
from pathlib import Path

import pytest

from src.services.field_normalizer import FieldNormalizer
from src.services.output_normalizer import OutputNormalizer

SCHEMATA = (
    Path(__file__).resolve().parents[1] / "src" / "schemata" / "mds_oeh" / "v2.0.0"
)

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


def systems_for(field_id):
    """Every schema that declares the field, so the check is not event-only."""
    out = []
    for path in sorted(SCHEMATA.glob("*.json")):
        if path.name == "manifest.json":
            continue
        schema = json.loads(path.read_text(encoding="utf-8"))
        for field in schema.get("fields") or []:
            if field.get("id") == field_id:
                out.append((path.stem, field["system"]))
    return out


@pytest.mark.parametrize("field_id", FORMER_LIST)
def test_the_field_normalizer_matches_the_old_person_list(field_id):
    places = systems_for(field_id)
    if not places:
        pytest.skip(f"{field_id} is not declared in mds_oeh/v2.0.0")

    old = OutputNormalizer()
    new = FieldNormalizer()
    for schema_name, system in places:
        for value in VALUES:
            expected = old._normalize_person_array(value)
            actual = new.normalize_field_value(value, system)
            assert actual == expected, (
                f"{schema_name}/{field_id} with {value!r}: "
                f"FieldNormalizer gave {actual!r}, the old list gave {expected!r}"
            )


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
