"""
/upload/verify compares a value the way its field is read (audit 14.11., R-N).

`values_match` lower-cased everything: a title stored as 'bruchrechnung' for a
sent 'Bruchrechnung' was a match. Now by field kind:

- free text (no vocabulary in the schema): the text itself counts, upper and
  lower case included; padding does not.
- vocabulary fields (URIs, keys): case is ignored as before - the repository
  resolves them that way.
- lists: order is ignored for every multi-valued field. All of them are sets
  in the schemata (keywords, vocabulary values, authors); how often a value
  occurs still counts.
"""

import pytest

from src.services.repository_diff import compute_diff, values_match

MARKERS = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
}
DISCIPLINE = "http://w3id.org/openeduhub/vocabs/discipline"


def _status(field_id, expected, actual):
    diff, _ = compute_diff(
        {**MARKERS, field_id: expected}, {field_id: actual}, "default", "2.0.0"
    )
    return next(d["status"] for d in diff if d["field_id"] == field_id)


@pytest.mark.parametrize(
    "field_id, expected, actual",
    [
        ("cclom:title", "Bruchrechnung", "bruchrechnung"),
        ("cclom:general_description", "PDF zum Download", "pdf zum download"),
        ("cclom:general_keyword", ["Brüche", "Mathe"], ["brüche", "mathe"]),
        ("ccm:wwwurl", "https://example.org/Seite", "https://example.org/seite"),
    ],
)
def test_a_difference_in_case_is_a_difference_in_free_text(field_id, expected, actual):
    assert _status(field_id, expected, actual) == "mismatch"


@pytest.mark.parametrize(
    "field_id, expected, actual",
    [
        ("cclom:title", "Bruchrechnung", ["Bruchrechnung"]),
        ("cclom:title", "Bruchrechnung", "  Bruchrechnung "),
        ("cclom:title", {"label": "Bruchrechnung"}, "Bruchrechnung"),
        ("cclom:general_keyword", ["Mathe", "Brüche"], ["Brüche", "Mathe"]),
    ],
)
def test_free_text_that_differs_only_in_representation_matches(
    field_id, expected, actual
):
    assert _status(field_id, expected, actual) == "match"


@pytest.mark.parametrize(
    "field_id, expected, actual",
    [
        ("ccm:taxonid", f"{DISCIPLINE}/380", f"{DISCIPLINE.upper()}/380"),
        (
            "ccm:taxonid",
            [f"{DISCIPLINE}/380", f"{DISCIPLINE}/100"],
            [f"{DISCIPLINE}/100", f"{DISCIPLINE}/380"],
        ),
        ("ccm:commonlicense_key", "CC_BY_SA", "cc_by_sa"),
        ("ccm:commonlicense_ai_generated", True, "true"),
    ],
)
def test_a_vocabulary_value_matches_whatever_its_case_or_order(
    field_id, expected, actual
):
    assert _status(field_id, expected, actual) == "match"


def test_how_often_a_value_occurs_still_counts():
    assert _status("cclom:general_keyword", ["a", "a", "b"], ["a", "b"]) == "mismatch"


def test_a_boolean_matches_the_string_the_repository_stores():
    assert values_match(False, "false", fold_case=False)
    assert not values_match(False, "true", fold_case=False)


def test_values_match_itself_takes_the_field_kind():
    assert values_match("Titel", "titel") is True  # as a vocabulary value
    assert values_match("Titel", "titel", fold_case=False) is False
    assert values_match("Titel", " Titel ", fold_case=False) is True
