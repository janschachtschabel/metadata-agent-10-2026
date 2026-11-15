"""
/generate with nothing left to extract answers 200 with the record unchanged.

Audit 14.11.2026, L-31 (a regression of the deadline fix P-11): with no field
to ask the model for, the field tasks were an empty set, asyncio.wait refuses
one, and the request ended in a 500. Two request shapes get there: a complete
record with regenerate_fields naming only a field the schema does not have,
and a record whose every field the editor filled (origin "user").
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.config import Settings
from src.services import metadata_service as metadata_module
from src.services.llm_service import LLMService, reset_provider_gates
from src.services.metadata_generation import load_extraction_fields
from src.services.metadata_service import MetadataService
from conftest import use_settings

TEXT = "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin."


class _Gateway:
    def __init__(self):
        self.calls = []

    def handler(self, request):
        self.calls.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"value": "x"}'}}]}
        )


@pytest.fixture
def gateway(monkeypatch):
    """A real MetadataService and LLMService in front of a fake gateway."""
    reset_provider_gates()
    fake = _Gateway()
    cfg = Settings(_env_file=None, b_api_key="own-key", llm_retry_delay=0)
    use_settings(monkeypatch, cfg)
    llm = LLMService(llm_provider="b-api-openai", llm_model="gpt-6-luna")
    llm.http_client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(metadata_module, "get_llm_service", lambda **kw: llm)
    service = MetadataService()
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: service)
    monkeypatch.setattr(deps, "get_llm_service", lambda **kw: llm)
    yield fake
    reset_provider_gates()


def _complete_record() -> dict:
    fields = load_extraction_fields("default", "2.0.0", "event.json", True, [])
    return {field["id"]: "wert" for field in fields}


def _generate(client, existing_metadata, **body):
    payload = {
        "text": TEXT,
        "schema_file": "event.json",
        "version": "2.0.0",
        "enable_geocoding": False,
        "normalize": False,
        "existing_metadata": existing_metadata,
        **body,
    }
    return client.post("/generate", json=payload)


def _assert_unchanged(response, record, gateway):
    assert response.status_code == 200, response.text
    data = response.json()
    assert {key: data[key] for key in record} == record
    assert data["processing"]["status"] == "success"
    assert data["processing"]["fields_extracted"] == 0
    assert data["processing"]["fields_failed"] == 0
    assert gateway.calls == []


def test_a_complete_record_and_an_unknown_field_to_regenerate(gateway):
    record = _complete_record()

    with TestClient(main.app) as client:
        response = _generate(client, record, regenerate_fields=["typo:field"])

    _assert_unchanged(response, record, gateway)


def test_a_record_the_editor_filled_completely(gateway):
    record = _complete_record()
    origins = {field_id: "user" for field_id in record}

    with TestClient(main.app) as client:
        response = _generate(client, {**record, "_origins": origins})

    _assert_unchanged(response, record, gateway)
    assert set(response.json()["_origins"].values()) == {"user"}


@pytest.mark.asyncio
async def test_no_fields_and_a_deadline_give_an_empty_result(gateway):
    import asyncio

    llm = deps.get_llm_service()

    extraction = await llm.extract_fields_parallel(
        [], "Text", deadline=asyncio.get_running_loop().time() + 60
    )

    assert extraction["values"] == {}
    assert extraction["timed_out"] == []
