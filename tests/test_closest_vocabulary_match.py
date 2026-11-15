"""
"Did you mean …?" suggestions of /validate for values outside a closed vocabulary.

The URI test was a substring test: "a", "w3id" or "vocab" matched the first
concept whose URI contains them, so short or generic values got an arbitrary
suggestion. Only the URI's trailing path (the concept's own id) may match.

The search is suggest_concept of services/vocabulary_matching.py since the
audit of 14.11.2026 (L-34): one matcher for /generate and /validate.
"""

from src.services.vocabulary_matching import suggest_concept

CONCEPTS = [
    {
        "uri": "http://w3id.org/openeduhub/vocabs/discipline/460",
        "label": {"de": "Physik"},
    },
    {"uri": "http://w3id.org/vocab/de", "label": {"de": "Deutsch", "en": "German"}},
]


def test_a_short_value_inside_a_uri_suggests_nothing():
    assert suggest_concept("a", CONCEPTS) is None


def test_a_generic_uri_part_suggests_nothing():
    assert suggest_concept("w3id", CONCEPTS) is None


def test_the_concept_id_suggests_its_uri():
    assert suggest_concept("460", CONCEPTS) == CONCEPTS[0]["uri"]


def test_a_trailing_uri_path_suggests_its_uri():
    assert suggest_concept("Discipline/460", CONCEPTS) == CONCEPTS[0]["uri"]


def test_a_close_label_still_suggests_its_uri():
    assert suggest_concept("deutsch", CONCEPTS) == CONCEPTS[1]["uri"]
