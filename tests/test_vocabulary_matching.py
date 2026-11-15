"""
One matcher for vocabulary values, against the real 2.0.0 schemas (audit 14.11.2026).

- L-32: an answer of one to three characters was fuzzy-matched to a concept:
  5, 7, 8, 9 and "x" became FSK 0, "14" became FSK 12, [""] an occupation.
  Now an answer under four characters is a concept or nothing.
- L-34 (M-04): (a) "Deutsh" gave the label "Deutsch" and cost four calls, now
  `de` in one; (b) "German" from the model is `de` like from a caller; (c)
  "DE" is `de` on both model paths; (d) is_concept counts 3 and True by their
  string form and checks 0 and False like any value; (e) suggest_concept
  names the key also where a vocabulary has no URIs.

The path-by-path table is in test_vocabulary_matching_characterization.py.
"""

import json

import httpx
import pytest

from src.config import Settings
from src.services import vocabulary_matching
from src.services.field_normalizer import FieldNormalizer
from src.services.llm_service import LLMService, reset_provider_gates
from src.services.vocabulary_matching import (
    is_concept,
    match_caller_value,
    match_model_answer,
    suggest_concept,
)
from src.utils.schema_loader import load_schema
from conftest import use_settings

W3 = "http://w3id.org/openeduhub/vocabs/"
MATHS = W3 + "discipline/380"


def _field(schema_file, field_id):
    fields = load_schema("default", "2.0.0", schema_file)["fields"]
    return next(f for f in fields if f["id"] == field_id)


def _concepts(schema_file, field_id):
    return _field(schema_file, field_id)["system"]["vocabulary"]["concepts"]


FSK = _field("learning_material.json", "ccm:fskRating")
LANGUAGE = _field("core.json", "cclom:general_language")
OCCUPATION = _field("education_offer.json", "schema:occupationalCategory")


class _Gateway:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def handler(self, request):
        self.calls += 1
        content = json.dumps({"value": self.answer})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


async def _extract(monkeypatch, field, answer):
    """(value, number of calls) of extract_field for one model answer."""
    cfg = Settings(_env_file=None, b_api_key="own-key", llm_retry_delay=0)
    use_settings(monkeypatch, cfg)
    gateway = _Gateway(answer)
    service = LLMService(llm_provider="b-api-openai")
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway.handler)
    )
    _, value, error = await service.extract_field(field, "Text", language="de")
    assert error is None
    return value, gateway.calls


# ------------------------------------------------------------ L-32


@pytest.mark.parametrize("answer", [5, 7, 8, 9, "7", "x", "14", "15", "17"])
async def test_a_short_answer_that_is_no_age_rating_gives_no_value(monkeypatch, answer):
    value, calls = await _extract(monkeypatch, FSK, answer)

    assert value is None
    # No concept: the model is asked again, as for any invalid answer.
    assert calls > 1


@pytest.mark.parametrize("answer", [12, "12", "16", W3 + "fskRating/12"])
async def test_a_short_answer_that_is_an_age_rating_is_taken(monkeypatch, answer):
    value, calls = await _extract(monkeypatch, FSK, answer)

    assert value == W3 + f"fskRating/{str(answer)[-2:]}"
    assert calls == 1


@pytest.mark.parametrize("answer", [[""], ["?"], ["x"]])
async def test_an_empty_entry_names_no_occupation(monkeypatch, answer):
    value, _ = await _extract(monkeypatch, OCCUPATION, answer)

    assert value == []


@pytest.mark.parametrize("answer", ["", "7", "ab", "abc", 7])
def test_no_answer_under_four_characters_is_matched_by_distance(answer):
    vocabulary = {
        "type": "closed",
        "concepts": [
            {"uri": "u/1", "label": {"de": "0"}},
            {"uri": "u/2", "label": {"de": "abd"}},
        ],
    }

    assert match_model_answer(answer, vocabulary) is None


# ------------------------------------------------------------ L-34 a-c


@pytest.mark.parametrize(
    "answer", ["Deutsh", "German", "german", "DE", " de ", "Deutsch", "de"]
)
async def test_a_language_answer_is_the_code_in_one_call(monkeypatch, answer):
    value, calls = await _extract(monkeypatch, LANGUAGE, answer)

    assert (value, calls) == ("de", 1)


@pytest.mark.parametrize("answer", ["Deutsh", "German", "DE", " de ", "Deutsch"])
def test_the_field_normalizer_gives_a_model_answer_the_same_key(answer):
    normalized = FieldNormalizer().normalize_field_value(answer, LANGUAGE["system"])

    assert normalized == "de"


@pytest.mark.parametrize(
    "sent, kept",
    [
        ("de", "de"),
        ("German", "de"),
        ("deutsch", "de"),
        (" de ", "de"),
        # Exact only: no other case, no nearest label, unknown values stay.
        ("DE", "DE"),
        ("Deutsh", "Deutsh"),
        ("xx-legacy", "xx-legacy"),
    ],
)
def test_a_caller_value_is_matched_exactly_or_kept(sent, kept):
    assert match_caller_value(sent, LANGUAGE["system"]["vocabulary"]) == kept


def test_no_path_returns_a_label_for_a_concept_with_a_key():
    licence = _field("learning_material.json", "ccm:commonlicense_key")
    vocabulary = licence["system"]["vocabulary"]

    assert match_model_answer("CC BY-SB", vocabulary) == "CC_BY_SA"
    assert match_model_answer("cc by", vocabulary) == "CC_BY"
    assert match_caller_value("CC BY", vocabulary) == "CC_BY"


def test_caller_values_never_reach_the_distance_search(monkeypatch):
    def refuse(text, labels):
        raise AssertionError("fuzzy search for a caller value")

    monkeypatch.setattr(vocabulary_matching, "nearest_label", refuse)
    vocabulary = _field("core.json", "ccm:taxonid")["system"]["vocabulary"]

    assert match_caller_value("Mathematk", vocabulary) == "Mathematk"


# ------------------------------------------------------------ L-34 d: numbers


QUALITY = _concepts("core.json", "ccm:oeh_quality_correctness")
AI_GENERATED = _concepts("core.json", "ccm:commonlicense_ai_generated")


@pytest.mark.parametrize(
    "value, concepts, expected",
    [
        (3, QUALITY, True),
        ("3", QUALITY, True),
        (0, QUALITY, True),
        (9, QUALITY, False),
        (3.0, QUALITY, False),
        (True, AI_GENERATED, True),
        (False, AI_GENERATED, True),
        ("true", AI_GENERATED, True),
        ("True", AI_GENERATED, False),
        (0, AI_GENERATED, False),
        (False, QUALITY, False),
        ("de", LANGUAGE["system"]["vocabulary"]["concepts"], True),
        ("DE", LANGUAGE["system"]["vocabulary"]["concepts"], False),
        ("Deutsch", LANGUAGE["system"]["vocabulary"]["concepts"], False),
        (MATHS, _concepts("core.json", "ccm:taxonid"), True),
        ("Mathematik", _concepts("core.json", "ccm:taxonid"), False),
        ({"uri": MATHS}, _concepts("core.json", "ccm:taxonid"), False),
        ([MATHS], _concepts("core.json", "ccm:taxonid"), False),
        (None, QUALITY, False),
    ],
)
def test_is_concept_counts_keys_and_numbers_by_their_string_form(
    value, concepts, expected
):
    assert is_concept(value, concepts) is expected


@pytest.mark.parametrize(
    "value, expected", [(0, "0"), (3, "3"), ("3", "3"), (9, None), (False, None)]
)
def test_zero_is_a_value_on_the_model_path(value, expected):
    vocabulary = _field("core.json", "ccm:oeh_quality_correctness")["system"][
        "vocabulary"
    ]

    assert match_model_answer(value, vocabulary) == expected


# ------------------------------------------------------------ L-34 e: suggestions


@pytest.mark.parametrize(
    "value, schema_file, field_id, expected",
    [
        # vocabularies keyed by `value`: there was never a suggestion
        ("Deutsh", "core.json", "cclom:general_language", "de"),
        ("Deutsch", "core.json", "cclom:general_language", "de"),
        ("German", "core.json", "cclom:general_language", "de"),
        ("DE", "core.json", "cclom:general_language", "de"),
        ("cc by", "learning_material.json", "ccm:commonlicense_key", "CC_BY"),
        ("CC BY-SB", "learning_material.json", "ccm:commonlicense_key", "CC_BY_SA"),
        ("Klingonisch", "core.json", "cclom:general_language", None),
        # vocabularies keyed by URI: as before
        ("Mathematik", "core.json", "ccm:taxonid", MATHS),
        ("Mathematk", "core.json", "ccm:taxonid", MATHS),
        ("380", "core.json", "ccm:taxonid", MATHS),
        ("discipline/380", "core.json", "ccm:taxonid", MATHS),
        ("EventScheduled", "event.json", "schema:eventStatus")
        + ("https://schema.org/EventScheduled",),
        # short text is a concept or nothing; a number is no text
        ("7", "learning_material.json", "ccm:fskRating", None),
        ("14", "learning_material.json", "ccm:fskRating", None),
        (7, "learning_material.json", "ccm:fskRating", None),
        (12, "learning_material.json", "ccm:fskRating", W3 + "fskRating/12"),
        ("12", "learning_material.json", "ccm:fskRating", W3 + "fskRating/12"),
        # a value that is a concept needs no suggestion
        ("de", "core.json", "cclom:general_language", None),
        (MATHS, "core.json", "ccm:taxonid", None),
        (3, "core.json", "ccm:oeh_quality_correctness", None),
        ("", "core.json", "ccm:taxonid", None),
        ("/", "core.json", "ccm:taxonid", None),
        ({"uri": MATHS}, "core.json", "ccm:taxonid", None),
    ],
)
def test_suggest_concept_names_the_key_that_was_meant(
    value, schema_file, field_id, expected
):
    assert suggest_concept(value, _concepts(schema_file, field_id)) == expected


def test_a_generic_part_of_a_uri_suggests_nothing():
    concepts = _concepts("core.json", "ccm:taxonid")

    assert suggest_concept("w3id", concepts) is None
    assert suggest_concept("vocabs", concepts) is None


# ------------------------------------------------------------ the index


def test_a_concept_list_is_indexed_once(monkeypatch):
    built = []
    original = vocabulary_matching._Index

    def counting(concepts):
        built.append(len(concepts))
        return original(concepts)

    monkeypatch.setattr(vocabulary_matching, "_Index", counting)
    first = [{"uri": "u/1", "label": {"de": "Eins"}}]
    second = [{"uri": "u/2", "label": {"de": "Zwei"}}]

    for _ in range(50):
        assert is_concept("u/1", first)
        assert is_concept("u/2", second)

    assert built == [1, 1]


def test_a_list_that_grew_is_indexed_again():
    concepts = [{"uri": "u/1", "label": {"de": "Eins"}}]
    assert not is_concept("u/2", concepts)

    concepts.append({"uri": "u/2", "label": {"de": "Zwei"}})

    assert is_concept("u/2", concepts)
