"""
One input, every path that matches it to a vocabulary (audit 14.11.2026, L-34 / M-04).

The table below was measured on 3.0.1 before the matching moved into
services/vocabulary_matching.py: the model answer path (LLMService), the
FieldNormalizer with full matching (model answers in /generate and
/extract-field) and the FieldNormalizer for caller values. `valid` is the check
that decides whether the model is asked again.

UNCHANGED rows hold today as they did then. DECIDED rows differ, each by one
of the decisions named in DECISIONS - a difference without a decision is a
regression.
"""

import pytest

from src.services.field_normalizer import FieldNormalizer
from src.services.llm_dates_numbers import DateNumberMixin
from src.services.llm_prompts import PromptMixin
from src.services.llm_values import ValueNormalizerMixin
from src.services.vocabulary_matching import is_valid_answer
from src.utils.schema_loader import load_schema

W3 = "http://w3id.org/openeduhub/vocabs/"
MATHS = W3 + "discipline/380"
PHYSICS = W3 + "discipline/460"
FSK_12 = W3 + "fskRating/12"
WORKSHEET = W3 + "new_lrt/36e68792-6159-481d-a97b-2c00901f4f78"
ZOO = W3 + "locationType/1b6f2d3c-4f6f-4a5f-9b3c-1c5a3d6f7c97"
FEDERAL = W3 + "jurisdictionLevel/federal"
SCHEDULED = "https://schema.org/EventScheduled"
APPLE = "http://example.org/a"
PRIMARY = "http://example.org/gs"

# Vocabularies the 2.0.0 schemas do not have: open with concepts, concepts
# with nothing but labels (the frozen 1.8.0 schemas), altLabels, number values.
SYNTHETIC = {
    "OPEN": {
        "type": "open",
        "concepts": [
            {"uri": APPLE, "label": {"de": "Apfel", "en": "Apple"}},
            {"uri": "http://example.org/b", "label": {"de": "Birne", "en": "Pear"}},
        ],
    },
    "LABEL_ONLY": {
        "type": "closed",
        "concepts": [
            {"label": {"de": "Einzelarbeit", "en": "Individual work"}},
            {"label": {"de": "Gruppenarbeit", "en": "Group work"}},
        ],
    },
    "ALT": {
        "type": "closed",
        "concepts": [
            {
                "uri": PRIMARY,
                "label": {"de": "Grundschule", "en": "Primary school"},
                "altLabels": ["Primarstufe", "Volksschule"],
            },
            {"uri": "http://example.org/sek1", "label": {"de": "Sekundarstufe I"}},
        ],
    },
    "NUMBERS": {
        "type": "closed",
        "concepts": [{"value": n, "label": {"de": str(n)}} for n in (1, 2, 3)],
    },
}

LM, CORE, EVENT = "learning_material.json", "core.json", "event.json"
FSK, LANGUAGE, LICENCE = (
    "ccm:fskRating",
    "cclom:general_language",
    "ccm:commonlicense_key",
)
QUALITY, AI, SUBJECT = (
    "ccm:oeh_quality_correctness",
    "ccm:commonlicense_ai_generated",
    "ccm:taxonid",
)
STATUS, LEVEL = "schema:eventStatus", "oeh:jurisdictionLevel"
OCCUPATION = "schema:occupationalCategory"

# (schema, field, value), (model answer, valid, full matching, caller value)
MEASURED: list[tuple[tuple, tuple]] = [
    ((LM, FSK, "7"), (W3 + "fskRating/0", True, None, "7")),
    ((LM, FSK, 7), (W3 + "fskRating/0", True, None, 7)),
    ((LM, FSK, "14"), (FSK_12, True, None, "14")),
    ((LM, FSK, "ab 12"), (None, False, None, "ab 12")),
    ((LM, FSK, "x"), (W3 + "fskRating/0", True, None, "x")),
    ((LM, FSK, ""), (W3 + "fskRating/0", True, "", "")),
    ((LM, FSK, "FSK 12"), (None, False, None, "FSK 12")),
    ((LM, FSK, FSK_12), (FSK_12, True, FSK_12, FSK_12)),
    ((CORE, LANGUAGE, "de"), ("de", True, "de", "de")),
    ((CORE, LANGUAGE, "DE"), ("de", True, None, "DE")),
    ((CORE, LANGUAGE, " de "), ("de", True, None, "de")),
    ((CORE, LANGUAGE, "German"), (None, False, None, "de")),
    ((CORE, LANGUAGE, "german"), (None, False, None, "de")),
    ((CORE, LANGUAGE, "Deutsch"), ("de", True, "de", "de")),
    ((CORE, LANGUAGE, "Deutsh"), ("Deutsch", False, "de", "Deutsh")),
    ((CORE, LANGUAGE, "Klingonisch"), (None, False, None, "Klingonisch")),
    ((CORE, LANGUAGE, "xx"), (None, False, None, "xx")),
    ((CORE, QUALITY, 3), ("3", True, "3", "3")),
    ((CORE, QUALITY, "3"), ("3", True, "3", "3")),
    ((CORE, QUALITY, 0), ("0", True, "0", "0")),
    ((CORE, QUALITY, 9), (None, False, None, 9)),
    ((CORE, QUALITY, 3.0), (None, False, None, 3.0)),
    ((CORE, AI, True), ("true", True, "true", "true")),
    ((CORE, AI, False), ("false", True, "false", "false")),
    ((CORE, AI, "TRUE"), ("true", True, None, "TRUE")),
    ((CORE, AI, "ja"), (None, False, None, "ja")),
    ((CORE, SUBJECT, ("Mathematics",)), ([MATHS], True, [MATHS], [MATHS])),
    ((CORE, SUBJECT, ("Mathematik",)), ([MATHS], True, [MATHS], [MATHS])),
    ((CORE, SUBJECT, ("mathematik ",)), ([MATHS], True, [MATHS], [MATHS])),
    ((CORE, SUBJECT, ("Mathematk",)), ([MATHS], True, [MATHS], ["Mathematk"])),
    ((CORE, SUBJECT, ("Mathematcs",)), ([MATHS], True, [MATHS], ["Mathematcs"])),
    ((CORE, SUBJECT, (MATHS,)), ([MATHS], True, [MATHS], [MATHS])),
    ((CORE, SUBJECT, (" " + MATHS,)), ([None], False, [], [MATHS])),
    ((CORE, SUBJECT, (MATHS.upper(),)), ([None], False, [], [MATHS.upper()])),
    (
        (CORE, SUBJECT, (MATHS.replace("http:", "https:"),)),
        ([None], False, [], [MATHS.replace("http:", "https:")]),
    ),
    ((CORE, SUBJECT, (MATHS + "/",)), ([None], False, [], [MATHS + "/"])),
    ((CORE, SUBJECT, ("380",)), ([None], False, [], ["380"])),
    (
        (CORE, SUBJECT, ("Mathematik", "Quatschwert", "Physik")),
        (
            [MATHS, None, PHYSICS],
            True,
            [MATHS, PHYSICS],
            [MATHS, "Quatschwert", PHYSICS],
        ),
    ),
    ((CORE, SUBJECT, ("",)), ([None], False, [], [])),
    ((CORE, SUBJECT, ()), ([], True, [], [])),
    ((CORE, SUBJECT, (None, "Physik")), ([None, PHYSICS], True, [PHYSICS], [PHYSICS])),
    ((CORE, "ccm:oeh_lrt", ("Worksheet",)), ([None], False, [], ["Worksheet"])),
    (
        (CORE, "ccm:oeh_lrt", ("Arbeitsblatt",)),
        ([WORKSHEET], True, [WORKSHEET], [WORKSHEET]),
    ),
    ((LM, LICENCE, "cc by"), ("CC_BY", True, "CC_BY", "CC_BY")),
    ((LM, LICENCE, "CC BY 4.0"), (None, False, None, "CC BY 4.0")),
    ((LM, LICENCE, "CC_BY"), ("CC_BY", True, "CC_BY", "CC_BY")),
    ((LM, LICENCE, "cc_by"), ("CC_BY", True, "CC_BY", "cc_by")),
    ((LM, LICENCE, "CC BY-SB"), ("CC BY-SA", False, "CC_BY_SA", "CC BY-SB")),
    ((EVENT, STATUS, "EventScheduled"), (None, False, None, "EventScheduled")),
    ((EVENT, STATUS, SCHEDULED), (SCHEDULED, True, SCHEDULED, SCHEDULED)),
    ((EVENT, STATUS, "Geplant"), (SCHEDULED, True, SCHEDULED, SCHEDULED)),
    ((EVENT, STATUS, "Scheduled"), (None, False, None, SCHEDULED)),
    (
        ("organization.json", "oeh:locationType", ("Zoom",)),
        ([ZOO], True, [ZOO], ["Zoom"]),
    ),
    (("curriculum.json", LEVEL, "Band"), (FEDERAL, True, FEDERAL, "Band")),
    (("curriculum.json", LEVEL, "Bund"), (FEDERAL, True, FEDERAL, FEDERAL)),
    (("education_offer.json", OCCUPATION, ("",)), ([W3 + "kldb/8112"], True, [], [])),
    (
        ("education_offer.json", OCCUPATION, ("?",)),
        ([W3 + "kldb/8112"], True, [], ["?"]),
    ),
    (("OPEN", "", "Apfel"), (APPLE, True, APPLE, APPLE)),
    (("OPEN", "", "Apple"), ("Apple", False, "Apple", APPLE)),
    (("OPEN", "", "apfel"), (APPLE, True, APPLE, APPLE)),
    (("OPEN", "", "Apfell"), ("Apfell", False, "Apfell", "Apfell")),
    (("OPEN", "", "Kirsche"), ("Kirsche", False, "Kirsche", "Kirsche")),
    (("OPEN", "", APPLE), (APPLE, True, APPLE, APPLE)),
    (("OPEN", "", 7), (7, False, 7, 7)),
    (
        ("LABEL_ONLY", "", "Einzelarbeit"),
        ("Einzelarbeit", False, "Einzelarbeit", "Einzelarbeit"),
    ),
    (
        ("LABEL_ONLY", "", "einzelarbeit"),
        ("Einzelarbeit", False, "Einzelarbeit", "Einzelarbeit"),
    ),
    (("LABEL_ONLY", "", "Individual work"), (None, False, None, "Einzelarbeit")),
    (
        ("LABEL_ONLY", "", "Einzelarbiet"),
        (
            "Einzelarbeit",
            False,
            {"de": "Einzelarbeit", "en": "Individual work"},
            "Einzelarbiet",
        ),
    ),
    (("LABEL_ONLY", "", "Plenum"), (None, False, None, "Plenum")),
    (("ALT", "", "Primarstufe"), (PRIMARY, True, PRIMARY, PRIMARY)),
    (("ALT", "", "volksschule"), (PRIMARY, True, PRIMARY, PRIMARY)),
    (("ALT", "", "Primarstuffe"), (None, False, None, "Primarstuffe")),
    (("ALT", "", "Primary school"), (None, False, None, PRIMARY)),
    (
        ("ALT", "", "Sekundarstufe 1"),
        ("http://example.org/sek1", True, "http://example.org/sek1", "Sekundarstufe 1"),
    ),
    (("NUMBERS", "", 2), (2, True, 2, 2)),
    (("NUMBERS", "", "2"), (2, True, 2, 2)),
    (("NUMBERS", "", 5), ("1", False, None, 5)),
    (("NUMBERS", "", 0), ("1", False, None, 0)),
    ((LM, FSK, 12), (FSK_12, True, FSK_12, 12)),
    ((CORE, SUBJECT, ({"uri": MATHS},)), ([None], False, [], [{"uri": MATHS}])),
    ((CORE, SUBJECT, (["Physik"],)), ([None], False, [[PHYSICS]], [["Physik"]])),
]


DECISIONS = {
    "key": "every path returns the concept key (URI, else value), never a label",
    "any-language": "a model answer may name a concept by its label in any language",
    "any-case": "a model answer may spell a `value` in any case; on both model paths",
    "blanks": "blanks around a key in a model answer do not matter, as for a caller",
    "short": "no fuzzy match under four characters, never for an empty answer (L-32)",
    "string-form": "numbers and booleans count by their string form on every path",
    "label-only": "a concept with neither URI nor value is its German label - a valid answer",
    "nested": "a list inside a list is no concept, on any path",
}

# (schema, field, value), decision, (model answer, valid, full matching, caller value)
DECIDED: list[tuple[tuple, str, tuple]] = [
    ((LM, FSK, "7"), "short", (None, False, None, "7")),
    ((LM, FSK, 7), "short", (None, False, None, 7)),
    ((LM, FSK, "14"), "short", (None, False, None, "14")),
    ((LM, FSK, "x"), "short", (None, False, None, "x")),
    ((LM, FSK, ""), "short", (None, False, "", "")),
    ((LM, FSK, 12), "string-form", (FSK_12, True, FSK_12, FSK_12)),
    ((CORE, LANGUAGE, "DE"), "any-case", ("de", True, "de", "DE")),
    ((CORE, LANGUAGE, " de "), "any-case", ("de", True, "de", "de")),
    ((CORE, LANGUAGE, "German"), "any-language", ("de", True, "de", "de")),
    ((CORE, LANGUAGE, "german"), "any-language", ("de", True, "de", "de")),
    ((CORE, LANGUAGE, "Deutsh"), "key", ("de", True, "de", "Deutsh")),
    ((CORE, AI, "TRUE"), "any-case", ("true", True, "true", "TRUE")),
    ((CORE, SUBJECT, (" " + MATHS,)), "blanks", ([MATHS], True, [MATHS], [MATHS])),
    ((CORE, SUBJECT, (["Physik"],)), "nested", ([None], False, [], [["Physik"]])),
    ((LM, LICENCE, "CC BY-SB"), "key", ("CC_BY_SA", True, "CC_BY_SA", "CC BY-SB")),
    (
        (EVENT, STATUS, "Scheduled"),
        "any-language",
        (SCHEDULED, True, SCHEDULED, SCHEDULED),
    ),
    (("education_offer.json", OCCUPATION, ("",)), "short", ([None], False, [], [])),
    (
        ("education_offer.json", OCCUPATION, ("?",)),
        "short",
        ([None], False, [], ["?"]),
    ),
    (("OPEN", "", "Apple"), "any-language", (APPLE, True, APPLE, APPLE)),
    (
        ("LABEL_ONLY", "", "Einzelarbeit"),
        "label-only",
        ("Einzelarbeit", True, "Einzelarbeit", "Einzelarbeit"),
    ),
    (
        ("LABEL_ONLY", "", "einzelarbeit"),
        "label-only",
        ("Einzelarbeit", True, "Einzelarbeit", "Einzelarbeit"),
    ),
    (
        ("LABEL_ONLY", "", "Individual work"),
        "any-language",
        ("Einzelarbeit", True, "Einzelarbeit", "Einzelarbeit"),
    ),
    (
        ("LABEL_ONLY", "", "Einzelarbiet"),
        "label-only",
        ("Einzelarbeit", True, "Einzelarbeit", "Einzelarbiet"),
    ),
    (("ALT", "", "Primary school"), "any-language", (PRIMARY, True, PRIMARY, PRIMARY)),
    (("NUMBERS", "", 5), "short", (None, False, None, 5)),
    (("NUMBERS", "", 0), "short", (None, False, None, 0)),
]

# By repr: as keys of a dict, 3 and 3.0 or 1 and True would be one case.
_MEASURED = {repr(case): measured for case, measured in MEASURED}
_DECIDED = {repr(case) for case, _, _ in DECIDED}


class _Values(ValueNormalizerMixin, DateNumberMixin, PromptMixin):
    """The value normalisation of LLMService without its settings and client."""


def _field(schema_file, field_id):
    if schema_file in SYNTHETIC:
        return {
            "id": "x:field",
            "system": {"datatype": "string", "vocabulary": SYNTHETIC[schema_file]},
        }
    for name in (schema_file, "core.json"):
        for field in load_schema("default", "2.0.0", name)["fields"]:
            if field["id"] == field_id:
                return field
    raise KeyError(field_id)


def _now(schema_file, field_id, value):
    """(model answer, valid, full matching, caller value) as the code answers now."""
    value = list(value) if isinstance(value, tuple) else value
    field = _field(schema_file, field_id)
    system = field["system"]
    answer = _Values()._normalize_value(value, field)
    normalizer = FieldNormalizer()
    return (
        answer,
        is_valid_answer(answer, system["vocabulary"]),
        normalizer.normalize_field_value(value, system, True, exact_only=False),
        normalizer.normalize_field_value(value, system, True, exact_only=True),
    )


def _id(value):
    if isinstance(value, tuple) and len(value) == 3 and isinstance(value[0], str):
        return f"{value[1] or value[0]} <- {value[2]!r}"[:70]
    return ""


@pytest.mark.parametrize(
    "case, measured", [row for row in MEASURED if repr(row[0]) not in _DECIDED], ids=_id
)
def test_these_inputs_give_what_3_0_1_gave(case, measured):
    assert _now(*case) == measured


@pytest.mark.parametrize("case, decision, expected", DECIDED, ids=_id)
def test_these_inputs_changed_by_a_decision(case, decision, expected):
    assert decision in DECISIONS
    assert expected != _MEASURED[repr(case)], "no change: the row belongs above"
    assert _now(*case) == expected


def test_a_caller_value_changed_in_one_case_only():
    # Caller values are matched exactly as in 2.1.1; the one difference is a
    # number that names a concept by its label (12 for the label "12").
    changed = [
        case for case, _, expected in DECIDED if expected[3] != _MEASURED[repr(case)][3]
    ]

    assert changed == [(LM, FSK, 12)]
