"""
A field extracted on its own comes back in the shape /generate gives it.

Audit 13.11., L-21: /extract-field ran the field normaliser only. Opening
hours came back with "Montag" where /generate answers ["MO"], and a place
came back without coordinates - saved from the form, the node then had no
latitude and longitude.
"""

from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.api.routers import extract as extract_module


class _LLM:
    provider, model = "b-api-openai", "gpt-6-luna"

    def __init__(self, value):
        self.value = value

    async def extract_field(self, field, text, existing_value, language):
        return field["id"], self.value, None

    async def close(self):
        pass


class _Geocoder:
    def __init__(self):
        self.asked = 0

    async def enrich_metadata_with_geocoding(self, metadata, language="de", **kwargs):
        self.asked += 1
        return {
            **metadata,
            "schema:location": [
                {
                    "name": "Bauhaus-Museum Weimar",
                    "geo": {"latitude": 50.98, "longitude": 11.32},
                }
            ],
        }


def _extract(monkeypatch, field_id, raw, **body):
    monkeypatch.setattr(deps, "get_llm_service", lambda **kw: _LLM(raw))
    with TestClient(main.app) as client:
        response = client.post(
            "/extract-field",
            json={
                "text": "Geöffnet montags. Bauhaus-Museum Weimar.",
                "schema_file": "event.json",
                "field_id": field_id,
                **body,
            },
        )
    assert response.status_code == 200, response.text
    return response.json()


def test_opening_hours_get_the_short_day_codes(monkeypatch):
    raw = [{"dayOfWeek": "Montag", "opens": "10:00", "closes": "18:00"}]

    body = _extract(
        monkeypatch,
        "schema:openingHoursSpecification",
        raw,
        schema_file="organization.json",
        version="1.8.1",
    )

    assert body["value"][0]["dayOfWeek"] == ["MO"]
    assert body["normalized"] is True


def test_a_place_gets_its_coordinates(monkeypatch):
    geocoder = _Geocoder()
    monkeypatch.setattr(extract_module, "get_geocoding_service", lambda: geocoder)

    body = _extract(monkeypatch, "schema:location", [{"name": "Bauhaus-Museum Weimar"}])

    assert body["value"][0]["geo"] == {"latitude": 50.98, "longitude": 11.32}
    assert geocoder.asked == 1


def test_no_other_field_asks_the_geocoder(monkeypatch):
    geocoder = _Geocoder()
    monkeypatch.setattr(extract_module, "get_geocoding_service", lambda: geocoder)

    _extract(monkeypatch, "cclom:title", "Ein Titel")

    assert geocoder.asked == 0


def test_without_normalising_the_raw_answer_is_returned(monkeypatch):
    geocoder = _Geocoder()
    monkeypatch.setattr(extract_module, "get_geocoding_service", lambda: geocoder)
    raw = [{"name": "Bauhaus-Museum Weimar"}]

    body = _extract(monkeypatch, "schema:location", raw, normalize=False)

    assert body["value"] == raw
    assert geocoder.asked == 0
