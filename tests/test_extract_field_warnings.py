"""
/extract-field says what it left out, as /generate does (audit 14.11.2026,
L-45 and the small items of the logic strand).

Three kinds of note reached `processing.warnings` of /generate only: entries
of a list a single-value field does not take, entries of the opening hours
that are no objects, and a date the model wrote month first. A field
extracted on its own lost the same values without a word.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.config import Settings
from src.services.llm_service import LLMService, reset_provider_gates
from conftest import use_settings


class _LLM:
    provider, model = "b-api-openai", "gpt-6-luna"

    def __init__(self, value):
        self.value = value

    async def extract_field(self, field, text, existing_value, language):
        return field["id"], self.value, None

    async def close(self):
        pass


def _extract(field_id, schema_file, **body):
    with TestClient(main.app) as client:
        response = client.post(
            "/extract-field",
            json={
                "text": "Ein Text.",
                "schema_file": schema_file,
                "field_id": field_id,
                **body,
            },
        )
    assert response.status_code == 200, response.text
    return response.json()


def test_values_a_single_value_field_does_not_take_are_counted(monkeypatch):
    answer = ["Erster Titel", "Zweiter Titel", "Dritter Titel"]
    monkeypatch.setattr(deps, "get_llm_service", lambda **kw: _LLM(answer))

    body = _extract("cclom:title", "core.json")

    assert body["value"] == "Erster Titel"
    assert body["processing"]["warnings"] == [
        "cclom:title: Feld nimmt einen Wert auf: 2 weitere Wert(e) nicht übernommen."
    ]


def test_opening_hours_that_are_no_objects_are_counted(monkeypatch):
    answer = [{"dayOfWeek": "Montag", "opens": "10:00", "closes": "18:00"}, 7]
    monkeypatch.setattr(deps, "get_llm_service", lambda **kw: _LLM(answer))

    body = _extract(
        "schema:openingHoursSpecification", "organization.json", version="1.8.1"
    )

    assert [entry["dayOfWeek"] for entry in body["value"]] == [["MO"]]
    assert body["processing"]["warnings"] == [
        "schema:openingHoursSpecification: 1 Wert(e) ohne Objektform nicht übernommen."
    ]


@pytest.fixture
def gateway_answer(monkeypatch):
    """A real LLMService whose gateway answers with the value set on it."""
    reset_provider_gates()
    cfg = Settings(_env_file=None, b_api_key="own-key", llm_retry_delay=0)
    use_settings(monkeypatch, cfg)
    service = LLMService(llm_provider="b-api-openai")

    def handler(request):
        content = json.dumps({"value": service.answer})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    service.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(deps, "get_llm_service", lambda **kw: service)
    yield service
    reset_provider_gates()


def test_a_date_the_model_wrote_month_first_is_named(gateway_answer):
    gateway_answer.answer = "03/15/2025"

    body = _extract("schema:foundingDate", "organization.json")

    assert body["value"] is None
    assert body["processing"]["warnings"] == [
        "schema:foundingDate: '03/15/2025' ist kein Datum in der Lesart "
        "Tag/Monat/Jahr und wurde nicht übernommen."
    ]


def test_an_answer_taken_as_it_is_has_no_warning(monkeypatch):
    monkeypatch.setattr(deps, "get_llm_service", lambda **kw: _LLM("Ein Titel"))

    body = _extract("cclom:title", "core.json")

    assert body["value"] == "Ein Titel"
    assert body["processing"]["warnings"] == []
