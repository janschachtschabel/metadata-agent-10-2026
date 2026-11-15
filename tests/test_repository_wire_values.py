"""
The form of a value on the wire to the repository (audit 14.11., list R-N).

- A list inside a list was written as its Python repr ("['a', 'b']").
- A text of nothing but spaces was written as a value.
- A JSON boolean went out as `[false]`; the repository's properties are
  strings, and WLO-REPO-FELDER.md asks for "true"/"false" (the form the
  editorial uploads write).
"""

import pytest

from src.services.repository_values import flatten_value, normalize_for_repo
from src.utils.schema_loader import get_repo_fields

FIELDS = {"cclom:title", "cclom:general_keyword", "ccm:commonlicense_ai_generated"}


def test_a_nested_list_is_written_as_its_values():
    result = normalize_for_repo({"cclom:general_keyword": [["a", "b"], "c"]}, FIELDS)

    assert result == {"cclom:general_keyword": ["a", "b", "c"]}


def test_objects_inside_a_nested_list_are_reduced_like_any_other():
    result = normalize_for_repo(
        {"cclom:general_keyword": [[{"label": "a"}], [None, ""], "b"]}, FIELDS
    )

    assert result == {"cclom:general_keyword": ["a", "b"]}


@pytest.mark.parametrize("blank", ["   ", "\n\t", ["  ", ""], [[" "]], {"label": " "}])
def test_a_text_of_nothing_but_whitespace_is_not_written(blank):
    assert normalize_for_repo({"cclom:title": blank}, FIELDS) == {}


def test_whitespace_entries_leave_the_real_ones():
    result = normalize_for_repo({"cclom:general_keyword": ["a", "  ", "b"]}, FIELDS)

    assert result == {"cclom:general_keyword": ["a", "b"]}


def test_a_text_keeps_its_own_whitespace():
    assert normalize_for_repo({"cclom:title": " Titel "}, FIELDS) == {
        "cclom:title": [" Titel "]
    }


@pytest.mark.parametrize(
    "value, wire",
    [
        (False, ["false"]),
        (True, ["true"]),
        ([True, False], ["true", "false"]),
        ({"value": False}, ["false"]),
    ],
)
def test_a_json_boolean_is_written_as_the_string_the_repository_stores(value, wire):
    result = normalize_for_repo({"ccm:commonlicense_ai_generated": value}, FIELDS)

    assert result == {"ccm:commonlicense_ai_generated": wire}


def test_a_boolean_field_of_the_real_schema_goes_out_as_text():
    repo_fields = get_repo_fields("default", "2.0.0")

    result = normalize_for_repo({"ccm:commonlicense_ai_generated": False}, repo_fields)

    assert result == {"ccm:commonlicense_ai_generated": ["false"]}


@pytest.mark.parametrize("number", [0, 4, 3.5])
def test_numbers_stay_numbers(number):
    # 0 is a value (the lowest step of a quality scale), not "empty".
    assert normalize_for_repo({"cclom:title": number}, FIELDS) == {
        "cclom:title": [number]
    }


def test_flatten_value_itself_still_passes_simple_values_through():
    assert flatten_value(True) is True
