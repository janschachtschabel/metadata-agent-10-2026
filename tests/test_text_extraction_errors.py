"""
A failed URL text extraction names its cause.

Observed on 2026-10-14 in production: every URL extraction ended after about
ten seconds with HTTP 400 and the detail "Failed to fetch input: " - empty,
because httpx timeouts carry no message. A connect timeout, a slow service,
a refused connection and an HTTP error need different fixes; the answer has
to say which one it was.
"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.services import input_source_service as input_module
from src.services.input_source_service import (
    InputData,
    InputSourceService,
    TextExtractionError,
)

EXTRACTOR = "http://bildungs-api-services-full-text.b-api"


async def _public(url):
    return url


def _service(monkeypatch, handler) -> InputSourceService:
    monkeypatch.setattr(input_module, "require_public_http_url", _public)
    service = InputSourceService()
    service.settings = SimpleNamespace(text_extraction_api_url=EXTRACTOR)
    service.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return service


def _raising(exc):
    def handler(request):
        raise exc

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc, code, status",
    [
        (httpx.ConnectTimeout(""), "TEXT_EXTRACTION_CONNECT_TIMEOUT", 504),
        (httpx.ReadTimeout(""), "TEXT_EXTRACTION_TIMEOUT", 504),
        (httpx.ConnectError(""), "TEXT_EXTRACTION_UNREACHABLE", 502),
        (httpx.RemoteProtocolError(""), "TEXT_EXTRACTION_FAILED", 502),
    ],
)
async def test_transport_failures_are_named(monkeypatch, exc, code, status):
    service = _service(monkeypatch, _raising(exc))

    with pytest.raises(TextExtractionError) as raised:
        await service.fetch_from_url("https://example.org/page")

    assert raised.value.code == code
    assert raised.value.http_status == status
    assert "bildungs-api-services-full-text.b-api" in raised.value.message


@pytest.mark.asyncio
async def test_an_http_error_of_the_extractor_carries_its_status(monkeypatch):
    service = _service(monkeypatch, lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(TextExtractionError) as raised:
        await service.fetch_from_url("https://example.org/page")

    assert raised.value.code == "TEXT_EXTRACTION_HTTP_ERROR"
    assert "500" in raised.value.message


@pytest.mark.asyncio
async def test_a_page_without_text_is_its_own_error(monkeypatch):
    service = _service(
        monkeypatch, lambda request: httpx.Response(200, json={"text": ""})
    )

    with pytest.raises(TextExtractionError) as raised:
        await service.fetch_from_url("https://example.org/page")

    assert raised.value.code == "TEXT_EXTRACTION_EMPTY"
    assert raised.value.http_status == 422


# ------------------------------------------------------------------ endpoints


class _FailingInput:
    async def fetch_from_url(self, *args, **kwargs):
        raise TextExtractionError(
            "TEXT_EXTRACTION_CONNECT_TIMEOUT",
            "Textextraktionsdienst x nicht erreichbar",
            504,
        )


@pytest.mark.parametrize(
    "path, body",
    [
        ("/generate", {"input_source": "url", "source_url": "https://example.org"}),
        (
            "/detect-content-type",
            {"input_source": "url", "source_url": "https://example.org"},
        ),
        (
            "/extract-field",
            {
                "input_source": "url",
                "source_url": "https://example.org",
                "schema_file": "core.json",
                "field_id": "cclom:title",
            },
        ),
    ],
)
def test_every_endpoint_reports_the_named_cause(monkeypatch, path, body):
    monkeypatch.setattr(main, "get_input_source_service", lambda: _FailingInput())

    with TestClient(main.app) as client:
        response = client.post(path, json=body)

    assert response.status_code == 504, response.text
    data = response.json()
    assert data["error"]["code"] == "TEXT_EXTRACTION_CONNECT_TIMEOUT"
    assert "nicht erreichbar" in data["detail"]


# ---------------------------------------------- node_url: fallback to the node


async def _node(*args, **kwargs):
    return {
        "properties": {
            "cclom:title": ["Konzert in Weimar"],
            "ccm:wwwurl": ["https://example.org/konzert"],
        }
    }


async def _no_fulltext(*args, **kwargs):
    return None


def _node_service(monkeypatch, handler) -> InputSourceService:
    service = _service(monkeypatch, handler)
    monkeypatch.setattr(service, "fetch_node_metadata", _node)
    monkeypatch.setattr(service, "fetch_node_text_content", _no_fulltext)
    return service


@pytest.mark.asyncio
async def test_node_url_says_when_the_text_came_from_the_node_instead(
    monkeypatch, caplog
):
    service = _node_service(monkeypatch, _raising(httpx.ConnectTimeout("")))

    data = await service.fetch_from_node_url("node-1")

    assert data.text == "Konzert in Weimar"
    assert len(data.warnings) == 1
    assert "TEXT_EXTRACTION_CONNECT_TIMEOUT" in data.warnings[0]
    assert "Titel, Beschreibung und Schlagworten" in data.warnings[0]
    # The internal address goes to the log, not to the browser.
    assert "bildungs-api-services-full-text.b-api" not in data.warnings[0]
    assert "bildungs-api-services-full-text.b-api" in caplog.text


@pytest.mark.asyncio
async def test_node_url_with_page_text_has_nothing_to_report(monkeypatch):
    service = _node_service(
        monkeypatch, lambda request: httpx.Response(200, json={"text": "Seitentext"})
    )

    data = await service.fetch_from_node_url("node-1")

    assert data.text == "Seitentext"
    assert data.warnings == []


FALLBACK = "Webseitentext nicht abrufbar (Zeitüberschreitung)"


class _FallbackInput:
    async def fetch_from_node_url(self, *args, **kwargs):
        return InputData(text="Konzert in Weimar", warnings=[FALLBACK])


class _StubMetadataService:
    llm_service = None

    async def generate_metadata(self, **kwargs):
        return {
            "contextName": "default",
            "schemaVersion": "1.8.1",
            "metadataset": "event.json",
            "language": "de",
            "exportedAt": "2026-10-14T00:00:00+00:00",
            "metadata": {"cclom:title": "Konzert in Weimar"},
            "_origins": {"cclom:title": "ai"},
            "processing": {
                "status": "success",
                "success": True,
                "fields_extracted": 1,
                "fields_failed": 0,
                "fields_total": 1,
                "errors": [],
                "warnings": ["Geocoding failed: x"],
            },
        }

    async def detect_schema(self, *args, **kwargs):
        return {"method": "llm", "confidence": "high", "schema_file": "event.json"}


class _StubLLM:
    provider, model = "b-api-openai", "gpt-6-luna"

    async def extract_field(self, **kwargs):
        return "cclom:title", "Konzert in Weimar", None

    async def close(self):
        pass


def test_every_endpoint_passes_the_fallback_on(monkeypatch):
    monkeypatch.setattr(main, "get_input_source_service", lambda: _FallbackInput())
    monkeypatch.setattr(
        main, "get_metadata_service", lambda **kw: _StubMetadataService()
    )
    monkeypatch.setattr(main, "get_llm_service", lambda **kw: _StubLLM())
    node = {
        "input_source": "node_url",
        "node_id": "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9",
    }

    with TestClient(main.app) as client:
        generated = client.post("/generate", json={**node, "enable_geocoding": False})
        detected = client.post("/detect-content-type", json=node)
        field = client.post(
            "/extract-field",
            json={**node, "schema_file": "core.json", "field_id": "cclom:title"},
        )

    assert generated.status_code == 200, generated.text
    assert generated.json()["processing"]["warnings"] == [
        FALLBACK,
        "Geocoding failed: x",
    ]
    assert detected.json()["warnings"] == [FALLBACK]
    assert field.json()["processing"]["warnings"] == [FALLBACK]
