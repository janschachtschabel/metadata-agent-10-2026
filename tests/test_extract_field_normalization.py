"""
/extract-field answers with the LLM's value unless normalisation changed it.

The 3.0.0 split (audit M-01) briefly returned the normalised object even when it
compared equal - 3.0 became 3, 1 became True - while reporting
normalized: false. The rule from before the split holds again.
"""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.api.routers import extract as extract_module


class _LLM:
    provider, model = "b-api-openai", "test-model"

    def __init__(self, value):
        self.value = value

    async def extract_field(self, field, text, existing_value, language):
        return field["id"], self.value, None

    async def close(self):
        pass


def _extract(monkeypatch, raw, normalized):
    monkeypatch.setattr(deps, "get_llm_service", lambda **kw: _LLM(raw))
    monkeypatch.setattr(
        extract_module,
        "get_field_normalizer",
        lambda: SimpleNamespace(normalize_field_value=lambda **kw: normalized),
    )
    with TestClient(main.app) as client:
        response = client.post(
            "/extract-field",
            json={
                "text": "Platz für dreihundert Gäste.",
                "schema_file": "event.json",
                "field_id": "schema:maximumAttendeeCapacity",
            },
        )
    assert response.status_code == 200, response.text
    return response.json()


def test_an_equal_normalised_value_keeps_the_llms_value(monkeypatch):
    body = _extract(monkeypatch, raw=3.0, normalized=3)

    assert body["value"] == 3.0 and isinstance(body["value"], float)
    assert body["normalized"] is False
    assert body["raw_value"] is None


def test_a_changed_value_is_reported_as_normalised(monkeypatch):
    body = _extract(monkeypatch, raw="dreihundert", normalized=300)

    assert body["value"] == 300
    assert body["normalized"] is True
    assert body["raw_value"] == "dreihundert"
