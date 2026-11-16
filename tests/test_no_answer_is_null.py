"""
No answer is null - also in a field that offers no "keine Angabe".

Staging 16.11.: an event without a word about its costs came back as "nein
(kostenfrei)". The cost vocabulary has no option for "not stated", and the
prompt of ccm:price counted CC-licensed material as free of charge - which
says nothing about what taking part costs. The system prompt now says that
null is "keine Angabe" for choice fields too, and the cost prompts of events,
offers and organisations ask for null when the text says nothing.
"""

import json
from pathlib import Path

import pytest

from src.services.llm_service import LLMService

SCHEMATA = Path(__file__).resolve().parents[1] / "src" / "schemata"

# Where taking part costs money or not, independent of a licence.
COST_FIELDS = [
    ("default", "event.json", "ccm:price"),
    ("default", "education_offer.json", "ccm:price"),
    ("default", "organization.json", "ccm:price"),
    ("mds_oeh", "event.json", "ccm:price"),
    ("mds_oeh", "education_offer.json", "schema:isAccessibleForFree"),
    ("mds_oeh", "organization.json", "schema:isAccessibleForFree"),
    ("default", "event.json", "schema:offers"),
    ("mds_oeh", "event.json", "schema:offers"),
]


def _field(context: str, schema: str, field_id: str) -> dict:
    data = json.loads((SCHEMATA / context / "v2.0.0" / schema).read_text("utf-8"))
    return next(f for f in data["fields"] if f["id"] == field_id)


@pytest.mark.parametrize("language", ["de", "en"])
def test_the_system_prompt_calls_null_no_answer_for_choice_fields(language):
    prompt = LLMService.__new__(LLMService)._get_system_prompt(language)

    if language == "de":
        assert "keine Angabe" in prompt and "Auswahlfeld" in prompt
    else:
        assert "not stated" in prompt and "choice field" in prompt


@pytest.mark.parametrize("context, schema, field_id", COST_FIELDS)
@pytest.mark.parametrize("language", ["de", "en"])
def test_a_cost_prompt_asks_for_null_when_the_text_says_nothing(
    context, schema, field_id, language
):
    prompt = _field(context, schema, field_id)["prompt"][language]

    assert "null" in prompt


@pytest.mark.parametrize("context, schema, field_id", COST_FIELDS)
def test_a_licence_is_no_sign_of_free_participation(context, schema, field_id):
    prompt = _field(context, schema, field_id)["prompt"]

    assert "CC-lizenziert" not in prompt["de"] and "Open Access" not in prompt["de"]
    assert "CC-licensed" not in prompt["en"] and "open access" not in prompt["en"]


@pytest.mark.parametrize("context", ["default", "mds_oeh"])
def test_free_of_charge_is_no_reason_for_null_in_the_offers(context):
    """'kostenlos' names no price, and still is an answer: price 0."""
    prompt = _field(context, "event.json", "schema:offers")["prompt"]

    assert "weder einen Preis noch kostenlos" in prompt["de"]
    assert "neither a price nor free of charge" in prompt["en"]


def test_no_schema_prompt_carries_a_garbled_euro_sign():
    garbled = [
        str(path.relative_to(SCHEMATA))
        for path in SCHEMATA.rglob("*.json")
        if "â‚¬" in path.read_text("utf-8")
    ]

    assert garbled == []
