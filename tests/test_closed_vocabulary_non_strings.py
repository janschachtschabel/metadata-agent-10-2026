"""
Numbers and booleans in closed vocabularies (FieldNormalizer, review #15).

Non-string values passed closed vocabularies unchecked: 0 stayed 0 instead of
"0", True stayed True instead of "true", and 3 or 7 stayed on the 0/1 scale
of ccm:oeh_quality_relevancy_for_education. The LLM path already compares the
lowercased string form and returns the concept's own spelling; the import and
write path now does the same.
"""

import pytest

from src.services.field_normalizer import FieldNormalizer

SCALE = {
    "type": "closed",
    "concepts": [
        {"value": "0", "label": {"de": "nicht relevant"}},
        {"value": "1", "label": {"de": "relevant"}},
    ],
}
FLAGS = {
    "type": "closed",
    "concepts": [
        {"value": "true", "label": {"de": "ja"}},
        {"value": "false", "label": {"de": "nein"}},
    ],
}


def _normalize(value, vocabulary, datatype="string"):
    return FieldNormalizer().normalize_field_value(
        value, {"datatype": datatype, "vocabulary": vocabulary}
    )


@pytest.mark.parametrize("value, expected", [(0, "0"), (1, "1"), ("0", "0")])
def test_a_number_on_the_scale_gets_the_concept_spelling(value, expected):
    assert _normalize(value, SCALE) == expected


@pytest.mark.parametrize("value, expected", [(True, "true"), (False, "false")])
def test_a_boolean_gets_the_concept_spelling(value, expected):
    assert _normalize(value, FLAGS) == expected


@pytest.mark.parametrize("value", [3, 7, 0.5])
def test_a_number_off_the_scale_is_dropped(value):
    assert _normalize(value, SCALE) is None


def test_off_scale_numbers_are_dropped_from_a_list():
    result = FieldNormalizer().normalize_field_value(
        [0, 7, 1],
        {"datatype": "array", "multiple": True, "vocabulary": SCALE},
    )

    assert result == ["0", "1"]


def test_an_object_in_a_closed_vocabulary_is_dropped():
    assert _normalize({"value": "1"}, SCALE) is None


def test_an_open_vocabulary_keeps_a_number():
    open_vocabulary = {**SCALE, "type": "open"}

    assert _normalize(7, open_vocabulary) == 7
