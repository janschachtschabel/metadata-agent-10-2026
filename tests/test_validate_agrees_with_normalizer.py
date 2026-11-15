"""
/validate judges a vocabulary value as /generate normalises it (audit
14.11.2026, L-34 d and e).

The validator had its own comparison and its own suggestion search: it
proposed FSK 0 for "7" where the normalizer finds no concept, and proposed
nothing at all for vocabularies keyed by `value` - "Deutsh", "German" and "DE"
became `de` in /generate and were merely "not in closed vocabulary" here. Both
now ask services/vocabulary_matching.py.

The table holds the auditor's inputs: the validator's verdict next to what the
normalizer makes of a model answer. They agree when

- a value the validator accepts is a concept for the normalizer too,
- a value the normalizer turns into a concept is reported with exactly that
  concept as the suggestion, and the concept itself is accepted,
- a value the normalizer finds no concept for is reported.

The validator may know more than the normalizer - it also proposes the concept
whose URI ends in the value ("380", "EventScheduled") - never something else.
"""

import re

import pytest
from fastapi.testclient import TestClient

from src.services import metadata_validation
from src.services.field_normalizer import FieldNormalizer
from src.utils.schema_loader import load_schema

W3 = "http://w3id.org/openeduhub/vocabs/"
MATHS = W3 + "discipline/380"
WORKSHEET = W3 + "new_lrt/36e68792-6159-481d-a97b-2c00901f4f78"
ZOO = W3 + "locationType/1b6f2d3c-4f6f-4a5f-9b3c-1c5a3d6f7c97"
FEDERAL = W3 + "jurisdictionLevel/federal"
SCHEDULED = "https://schema.org/EventScheduled"

LM, CORE = "learning_material.json", "core.json"
FSK, LANGUAGE, LICENCE = (
    "ccm:fskRating",
    "cclom:general_language",
    "ccm:commonlicense_key",
)
QUALITY, AI, SUBJECT, LRT = (
    "ccm:oeh_quality_correctness",
    "ccm:commonlicense_ai_generated",
    "ccm:taxonid",
    "ccm:oeh_lrt",
)

OK = "ok"
UNKNOWN = "unknown"  # reported, no suggestion

# (schema, field, value), validator's verdict (OK, UNKNOWN or the suggested
# concept), normalizer's result for a model answer
TABLE: list[tuple[tuple, object, object]] = [
    ((LM, FSK, "7"), UNKNOWN, None),
    ((LM, FSK, 7), UNKNOWN, None),
    ((LM, FSK, "14"), UNKNOWN, None),
    ((LM, FSK, "ab 12"), UNKNOWN, None),
    ((CORE, LANGUAGE, "DE"), "de", "de"),
    ((CORE, LANGUAGE, "German"), "de", "de"),
    ((CORE, LANGUAGE, "Deutsch"), "de", "de"),
    ((CORE, LANGUAGE, "Deutsh"), "de", "de"),
    ((CORE, LANGUAGE, " de "), "de", "de"),
    ((CORE, QUALITY, 3), OK, "3"),
    ((CORE, QUALITY, 0), OK, "0"),
    ((CORE, QUALITY, 9), UNKNOWN, None),
    ((CORE, QUALITY, 3.0), UNKNOWN, None),
    ((CORE, AI, True), OK, "true"),
    ((CORE, AI, False), OK, "false"),
    ((CORE, SUBJECT, ["Mathematics"]), MATHS, [MATHS]),
    ((CORE, SUBJECT, ["Mathematik"]), MATHS, [MATHS]),
    ((CORE, SUBJECT, ["Mathematk"]), MATHS, [MATHS]),
    ((CORE, SUBJECT, [MATHS.replace("http:", "https:")]), UNKNOWN, []),
    ((CORE, SUBJECT, [MATHS + "/"]), UNKNOWN, []),
    ((CORE, SUBJECT, ["380"]), MATHS, []),
    ((CORE, LRT, ["Worksheet"]), UNKNOWN, []),
    ((CORE, LRT, ["Arbeitsblatt"]), WORKSHEET, [WORKSHEET]),
    ((LM, LICENCE, "cc by"), "CC_BY", "CC_BY"),
    ((LM, LICENCE, "CC BY 4.0"), UNKNOWN, None),
    ((LM, LICENCE, "CC_BY"), OK, "CC_BY"),
    (("event.json", "schema:eventStatus", "EventScheduled"), SCHEDULED, None),
    (("organization.json", "oeh:locationType", ["Zoom"]), ZOO, [ZOO]),
    (("curriculum.json", "oeh:jurisdictionLevel", "Band"), FEDERAL, FEDERAL),
]


def _system(schema_file, field_id):
    for name in (schema_file, "core.json"):
        for field in load_schema("default", "2.0.0", name)["fields"]:
            if field["id"] == field_id:
                return field["system"]
    raise KeyError(field_id)


def _verdict(field_id, system, value):
    """OK, UNKNOWN or the suggested concept, for a value or a one-entry list."""
    values = value if isinstance(value, list) else [value]
    warnings = metadata_validation._vocabulary_warnings(field_id, system, values)
    if not warnings:
        return OK
    assert len(warnings) == 1, warnings
    suggestion = re.search(r"Did you mean '(.*)'\?$", warnings[0]["message"])
    return suggestion.group(1) if suggestion else UNKNOWN


def _normalized(system, value):
    return FieldNormalizer().normalize_field_value(value, system, True)


def _id(row):
    (_, field_id, value), _, _ = row
    return f"{field_id}={value!r}"


@pytest.mark.parametrize("row", TABLE, ids=_id)
def test_the_verdict_and_the_normalised_value_are_as_measured(row):
    (schema_file, field_id, value), verdict, normalized = row
    system = _system(schema_file, field_id)

    assert _verdict(field_id, system, value) == verdict
    assert _normalized(system, value) == normalized


@pytest.mark.parametrize("row", TABLE, ids=_id)
def test_the_validator_agrees_with_the_normalizer(row):
    (schema_file, field_id, value), _, _ = row
    system = _system(schema_file, field_id)
    verdict = _verdict(field_id, system, value)
    normalized = _normalized(system, value)
    concept = normalized[0] if isinstance(normalized, list) and normalized else None
    if not isinstance(normalized, list):
        concept = normalized

    if verdict == OK:
        assert concept is not None
    if concept is None:
        assert verdict != OK
    else:
        # What /generate returns passes /validate ...
        assert _verdict(field_id, system, normalized) == OK
        # ... and where it differs from the input, it is what /validate proposes.
        assert verdict in (OK, concept)


def test_validate_suggests_the_key_of_a_vocabulary_without_uris():
    from src import main

    client = TestClient(main.app, raise_server_exceptions=False)
    response = client.post(
        "/validate",
        json={
            "metadata": {"cclom:title": "Titel", LANGUAGE: ["Deutsh"]},
            "schema_file": "core.json",
        },
    )

    assert response.status_code == 200, response.text
    messages = [
        w["message"] for w in response.json()["warnings"] if w["field_id"] == LANGUAGE
    ]
    assert messages == ["Value 'Deutsh' not in vocabulary. Did you mean 'de'?"]


@pytest.mark.parametrize("value, text", [(7, "7"), (True, "true")])
def test_a_number_or_boolean_is_quoted_as_json_writes_it(value, text):
    problems = metadata_validation._vocabulary_problems(_system(LM, FSK), [value])

    assert list(problems) == [f"Value '{text}' not in closed vocabulary"]
