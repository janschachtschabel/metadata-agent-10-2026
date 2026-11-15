"""
/validate: what counts as a filled required field, and how numbers and
booleans meet a closed vocabulary (audit 14.11., L-44 and L-34 d).

`[""]` and `"   "` passed as a filled title (`valid: true`, coverage 100). The
number 3 and `true` were reported as no concept although the vocabulary has "3"
and "true", while 0 and `false` were not looked at at all.
"""

import pytest

from src.services.metadata_service import get_metadata_service

TITLE = "cclom:title"  # required (core.json)
SCALE = "ccm:oeh_quality_correctness"  # closed: "0" … "5"
FLAG = "ccm:commonlicense_ai_generated"  # closed: "true", "false"


def _validate(metadata):
    return get_metadata_service().validate_metadata(
        metadata, context="default", version="2.0.0", schema_file="core.json"
    )


def _of(issues, field_id):
    return [issue["message"] for issue in issues if issue["field_id"] == field_id]


@pytest.mark.parametrize("value", [[""], "   ", [" ", ""], "\n\t", [None], {}, [{}]])
def test_nothing_but_blanks_is_no_filled_required_field(value):
    result = _validate({TITLE: value})

    assert result["valid"] is False
    assert _of(result["errors"], TITLE) == ["Required field is empty"]
    assert result["coverage"] == 0.0


@pytest.mark.parametrize("value", ["Titel", ["Titel"], [" ", "Titel"], 0, False])
def test_a_value_fills_it(value):
    result = _validate({TITLE: value})

    assert _of(result["errors"], TITLE) == []
    assert result["coverage"] == 100.0


@pytest.mark.parametrize(
    "field_id, value",
    [(SCALE, 3), (SCALE, 0), (SCALE, "3"), (FLAG, True), (FLAG, False), (FLAG, "true")],
)
def test_a_number_or_boolean_matches_the_concept_of_its_text(field_id, value):
    result = _validate({TITLE: "T", field_id: value})

    assert _of(result["warnings"], field_id) == []


@pytest.mark.parametrize(
    "field_id, value, quoted",
    [
        (SCALE, 9, "'9'"),
        (SCALE, -1, "'-1'"),
        (FLAG, 0, "'0'"),
        (SCALE, False, "'false'"),
    ],
)
def test_one_that_matches_none_is_reported_zero_and_false_included(
    field_id, value, quoted
):
    result = _validate({TITLE: "T", field_id: value})

    (warning,) = _of(result["warnings"], field_id)
    assert quoted in warning
    assert "vocabulary" in warning
