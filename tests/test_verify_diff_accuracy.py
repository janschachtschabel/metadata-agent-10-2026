"""
/upload/verify must say "match" only for what matches, and nothing else.

Found in the 2026-10-15 audit:
- lists with more than one entry were compared as str(dict), so every
  multi-valued vocabulary field read as a mismatch;
- any author name in the repository counted as the expected author;
- timestamps with an offset did not parse and showed as mismatches;
- any two numbers above 10^9 within 60 apart were "the same date".
"""

import pytest

from src.services.repository_diff import compute_diff, values_match

VOCAB = "http://w3id.org/openeduhub/vocabs/discipline"


def test_a_multi_valued_vocabulary_field_matches_its_uris():
    expected = [
        {"uri": f"{VOCAB}/460", "label": "Physik"},
        {"uri": f"{VOCAB}/240", "label": "Geschichte"},
    ]
    actual = [f"{VOCAB}/240", f"{VOCAB}/460"]

    assert values_match(expected, actual)


def test_a_different_set_of_uris_is_still_a_mismatch():
    expected = [{"uri": f"{VOCAB}/460"}, {"uri": f"{VOCAB}/240"}]

    assert not values_match(expected, [f"{VOCAB}/460", f"{VOCAB}/999"])


@pytest.mark.parametrize(
    "expected, actual",
    [
        ("2026-01-01T10:00:00+01:00", "1767258000000"),
        ("2026-01-01T09:00:00Z", "1767258000000"),
        ("2026-01-01", "1767225600000"),
    ],
)
def test_a_timestamp_with_offset_or_date_only_matches_the_epoch(expected, actual):
    assert values_match(expected, actual)


def test_two_plain_numbers_are_not_dates():
    assert not values_match("1700000000", "1700000030")


def _author_status(repo_name):
    diff, _ = compute_diff(
        {"metadataset": "learning_material.json", "cm:author": ["Erika Mustermann"]},
        {"ccm:lifecyclecontributer_authorFN": [repo_name]},
        "default",
        "latest",
    )
    return next(d["status"] for d in diff if d["field_id"] == "cm:author")


def test_the_expected_author_matches():
    assert _author_status("Erika Mustermann") == "match"


def test_another_author_is_a_mismatch():
    assert _author_status("Max Muster") == "mismatch"
