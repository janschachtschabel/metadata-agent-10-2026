"""
The API never presents a failed generation as a result.

Through the whole stack - middleware, endpoint, MetadataService, LLMService -
with only the B-API replaced by a fake. Covers what the WLO tests of
2026-10-14 ran into: 403 on every call, answered with 200 and success=true,
and a silent fallback to learning_material.json with confidence 'high'.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.config import Settings
from src.services import llm_service as llm_module
from src.services import metadata_service as metadata_module
from src.services.llm_errors import LLMUpstreamError
from src.services.llm_service import (
    ContentTypeDetection,
    LLMService,
    reset_provider_gates,
)
from src.services.metadata_service import MetadataService

FORBIDDEN = (
    '{"status":403,"error":"Forbidden","trace":"...AuthorizationDeniedException..."}'
)
EVENT_TEXT = "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin."


class _Gateway:
    def __init__(self, status=200, content='{"value": null}'):
        self.status, self.content = status, content
        self.calls = []

    def handler(self, request):
        self.calls.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text=FORBIDDEN)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": self.content}}]}
        )


@pytest.fixture
def stack(monkeypatch):
    """Wire a real MetadataService to a fake gateway; returns (client, gateway, settings)."""

    def build(gateway, **settings_overrides):
        reset_provider_gates()
        cfg = Settings(
            _env_file=None, b_api_key="own-key", llm_retry_delay=0, **settings_overrides
        )
        monkeypatch.setattr(llm_module, "get_settings", lambda: cfg)
        monkeypatch.setattr(main, "settings", cfg)
        llm = LLMService(llm_provider="b-api-openai", llm_model="gpt-6-luna")
        llm.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(gateway.handler)
        )
        monkeypatch.setattr(metadata_module, "get_llm_service", lambda **kw: llm)
        service = MetadataService()
        monkeypatch.setattr(main, "get_metadata_service", lambda **kw: service)
        monkeypatch.setattr(main, "get_llm_service", lambda **kw: llm)
        return service

    yield build
    reset_provider_gates()


def _generate(client, **body):
    payload = {"text": EVENT_TEXT, "enable_geocoding": False, **body}
    return client.post("/generate", json=payload)


# ------------------------------------------------------------------ /generate


def test_a_refused_key_is_a_502_with_a_code_not_a_success(stack):
    gateway = _Gateway(403)
    stack(gateway)

    with TestClient(main.app) as client:
        response = _generate(client)

    assert response.status_code == 502, response.text
    data = response.json()
    assert data["error"]["code"] == "LLM_AUTH_FORBIDDEN"
    assert "403" in data["detail"]
    assert data["processing"]["status"] == "failed"
    assert data["processing"]["success"] is False
    assert data["processing"]["fields_extracted"] == 0
    # The content-type call already failed: no field call was sent at all.
    assert len(gateway.calls) == 1


def test_with_a_preselected_schema_a_refused_key_still_stops_early(stack):
    gateway = _Gateway(403)
    stack(gateway)

    with TestClient(main.app) as client:
        response = _generate(client, schema_file="event.json", max_workers=5)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_AUTH_FORBIDDEN"
    assert len(gateway.calls) <= 5


def test_forward_mode_without_forwarded_key_is_a_502_with_a_clear_reason(stack):
    # 502, not 401: this answer travels back through edu-sharing, which must
    # not take it for an expired session of the user.
    gateway = _Gateway(200)
    stack(gateway, b_api_auth_mode="forward")

    with TestClient(main.app) as client:
        response = _generate(client)

    assert response.status_code == 502
    data = response.json()
    assert data["error"]["code"] == "LLM_AUTH_MISSING"
    assert data["processing"]["fields_failed"] == 1
    assert gateway.calls == []


def test_the_forwarded_key_reaches_every_b_api_call_unchanged(stack):
    gateway = _Gateway(200, '{"content_type": "event.json"}')
    stack(gateway)

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={"text": EVENT_TEXT, "enable_geocoding": False},
            headers={"Authorization": "Bearer user-key"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["processing"]["auth_source"] == "forwarded"
    assert gateway.calls
    for call in gateway.calls:
        assert call.headers["authorization"] == "Bearer user-key"
        assert "x-api-key" not in call.headers


def test_some_failed_fields_make_a_partial_result(stack, monkeypatch):
    service = stack(_Gateway(200))

    async def extract_field(
        field, text, existing_value=None, language="de", retry_count=0
    ):
        if field["id"] == "cclom:title":
            return field["id"], "KI in der Bildung", None
        return field["id"], None, LLMUpstreamError("HTTP 500", upstream_status=500)

    monkeypatch.setattr(service.llm_service, "extract_field", extract_field)

    with TestClient(main.app) as client:
        response = _generate(client, schema_file="event.json")

    assert response.status_code == 200
    processing = response.json()["processing"]
    assert processing["status"] == "partial"
    assert processing["success"] is True
    assert processing["fields_extracted"] == 1
    assert processing["fields_failed"] == processing["fields_total"] - 1


def test_all_fields_failing_without_a_fatal_error_is_still_a_failure(
    stack, monkeypatch
):
    service = stack(_Gateway(200))

    async def extract_field(
        field, text, existing_value=None, language="de", retry_count=0
    ):
        return field["id"], None, LLMUpstreamError("HTTP 500", upstream_status=500)

    monkeypatch.setattr(service.llm_service, "extract_field", extract_field)

    with TestClient(main.app) as client:
        response = _generate(client, schema_file="event.json")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_UPSTREAM_ERROR"


def test_an_unusable_content_type_answer_falls_back_openly(stack, monkeypatch):
    service = stack(_Gateway(200))

    async def detect(*args, **kwargs):
        return ContentTypeDetection(None, "unbekannte Inhaltsart 'recipe.json'")

    monkeypatch.setattr(service.llm_service, "detect_content_type", detect)

    with TestClient(main.app) as client:
        response = _generate(client)

    assert response.status_code == 200
    detection = response.json()["processing"]["content_type_detection"]
    assert detection["method"] == "keywords"
    assert detection["confidence"] == "low"
    assert "recipe.json" in detection["reason"]
    assert any("Inhaltsart" in w for w in response.json()["processing"]["warnings"])


def test_a_detected_content_type_is_reported_as_such(stack):
    stack(_Gateway(200, '{"content_type": "event.json"}'))

    with TestClient(main.app) as client:
        response = _generate(client)

    detection = response.json()["processing"]["content_type_detection"]
    assert response.json()["metadataset"] == "event.json"
    assert detection == {
        "method": "llm",
        "confidence": "high",
        "schema_file": "event.json",
    }


# ------------------------------------------------------- /detect-content-type


def test_detect_content_type_reports_a_refused_key_instead_of_learning_material(stack):
    stack(_Gateway(403))

    with TestClient(main.app) as client:
        response = client.post("/detect-content-type", json={"text": EVENT_TEXT})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_AUTH_FORBIDDEN"


def test_detect_content_type_labels_a_fallback_as_low_confidence(stack, monkeypatch):
    service = stack(_Gateway(200))

    async def detect(*args, **kwargs):
        return ContentTypeDetection(None, "keine auswertbare Antwort")

    monkeypatch.setattr(service.llm_service, "detect_content_type", detect)

    with TestClient(main.app) as client:
        response = client.post("/detect-content-type", json={"text": EVENT_TEXT})

    assert response.status_code == 200
    data = response.json()
    assert data["detected"]["confidence"] == "low"
    assert data["method"] == "keywords"
    assert data["warnings"]


# ------------------------------------------------------------- /extract-field


def test_extract_field_reports_a_refused_key_as_502_not_500(stack):
    stack(_Gateway(403))

    with TestClient(main.app) as client:
        response = client.post(
            "/extract-field",
            json={
                "text": EVENT_TEXT,
                "schema_file": "core.json",
                "field_id": "cclom:title",
            },
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_AUTH_FORBIDDEN"


# --------------------------------------------- a generation that raises


def test_a_raising_generation_closes_its_client_and_is_counted(stack, monkeypatch):
    from src.metrics import GENERATIONS

    service = stack(_Gateway())
    closed = []

    async def explode(**kwargs):
        raise RuntimeError("unexpected")

    async def close():
        closed.append(True)

    monkeypatch.setattr(service, "generate_metadata", explode)
    monkeypatch.setattr(service.llm_service, "close", close)
    failed = GENERATIONS.labels("failed")
    before = failed._value.get()

    with TestClient(main.app, raise_server_exceptions=False) as client:
        response = _generate(client, llm_model="gpt-6-luna")

    assert response.status_code == 500
    # The per-request client of an override must not outlive the request.
    assert closed == [True]
    assert failed._value.get() == before + 1
