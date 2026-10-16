"""
Infrastructure endpoints, as Team 6 asks for them.

- /health (liveness) and /ready (readiness) stay reachable on the public port,
  as before, but leave the OpenAPI spec: the spec describes the product.
- /ready says whether this instance can work at all: schemas loaded, an LLM
  key possible for the configured mode. A pod that cannot gets no traffic.
- /metrics (Prometheus) exists only on the internal port
  (METADATA_AGENT_INTERNAL_PORT), never on the public one. Without that
  setting nothing new is served.
"""

import asyncio
import socket

import httpx
import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from src import main
from src.config import Settings
from src.infra import (
    InternalPortUnavailable,
    create_internal_app,
    start_internal_server,
)
from src.services import llm_service as llm_module
from src.services.llm_errors import LLMAuthForbidden
from src.services.llm_service import LLMService, reset_provider_gates


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{"b_api_key": "own-key", **overrides})


def _sample(name, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ------------------------------------------------------------ public port


def test_ready_when_the_configuration_can_work(monkeypatch):
    monkeypatch.setattr(main, "settings", _settings())

    with TestClient(main.app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"b_api_auth_mode": "static", "b_api_key": ""}, "B_API_KEY"),
        ({"llm_provider": "openai", "openai_api_key": ""}, "OPENAI_API_KEY"),
    ],
)
def test_not_ready_when_no_llm_call_could_succeed(monkeypatch, overrides, reason):
    monkeypatch.setattr(main, "settings", _settings(**overrides))

    with TestClient(main.app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert any(reason in p for p in response.json()["problems"])


def test_forward_mode_without_own_key_is_ready(monkeypatch):
    # The key arrives with each request through the B-API proxy.
    monkeypatch.setattr(
        main, "settings", _settings(b_api_auth_mode="forward", b_api_key="")
    )

    with TestClient(main.app) as client:
        assert client.get("/ready").status_code == 200


def test_infrastructure_paths_are_not_in_the_spec():
    paths = main.app.openapi()["paths"]

    for path in ("/health", "/ready", "/metrics", "/debug/auth", "/debug/dependencies"):
        assert path not in paths


def test_health_is_still_served_on_the_public_port():
    # Docker HEALTHCHECK and existing probes use it.
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200


def test_no_metrics_on_the_public_port():
    with TestClient(main.app) as client:
        assert client.get("/metrics").status_code == 404


# ------------------------------------------------------------ internal port


def test_the_internal_app_serves_health_ready_and_metrics():
    internal = create_internal_app(lambda: _settings())

    with TestClient(internal) as client:
        assert client.get("/health").json()["status"] == "healthy"
        assert client.get("/ready").status_code == 200
        metrics = client.get("/metrics")
        # The internal app has no API description of its own.
        spec = client.get("/openapi.json")

    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain")
    assert "metadata_agent_http_requests_total" in metrics.text
    assert spec.status_code == 404


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_the_internal_server_listens_on_its_own_port_and_stops():
    port = _free_port()
    stop = await start_internal_server(
        create_internal_app(lambda: _settings()), port, host="127.0.0.1"
    )
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(50):
                try:
                    response = await client.get(f"http://127.0.0.1:{port}/health")
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.05)
        assert response.status_code == 200
    finally:
        await stop()

    with pytest.raises(httpx.ConnectError):
        async with httpx.AsyncClient() as client:
            await client.get(f"http://127.0.0.1:{port}/health")


# ------------------------------------------------------------ metrics


def test_http_requests_are_counted_by_route_template():
    before = _sample(
        "metadata_agent_http_requests_total",
        method="GET",
        route="/widget/i18n/{lang}.json",
        status="200",
    )

    with TestClient(main.app) as client:
        client.get("/widget/i18n/de.json")

    after = _sample(
        "metadata_agent_http_requests_total",
        method="GET",
        route="/widget/i18n/{lang}.json",
        status="200",
    )
    assert after == before + 1


@pytest.mark.asyncio
async def test_llm_calls_are_counted_by_result(monkeypatch):
    reset_provider_gates()
    cfg = _settings(llm_retry_delay=0)
    monkeypatch.setattr(llm_module, "get_settings", lambda: cfg)
    service = LLMService(llm_provider="b-api-openai")
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(403, text="Forbidden"))
    )
    labels = {"provider": "b-api-openai", "result": "LLM_AUTH_FORBIDDEN"}
    before = _sample("metadata_agent_llm_requests_total", **labels)

    with pytest.raises(LLMAuthForbidden):
        await service._call_llm([{"role": "user", "content": "x"}])

    assert _sample("metadata_agent_llm_requests_total", **labels) == before + 1
    reset_provider_gates()


class _Generation:
    llm_service = None

    async def generate_metadata(self, **kwargs):
        return {
            "contextName": "default",
            "schemaVersion": "1.8.1",
            "metadataset": "event.json",
            "language": "de",
            "exportedAt": "2026-10-14T00:00:00+00:00",
            "metadata": {"cclom:title": "Konzert"},
            "_origins": {},
            "processing": {"status": "partial", "success": True, "warnings": []},
        }


def test_generations_are_counted_by_status(monkeypatch):
    monkeypatch.setattr(main, "get_metadata_service", lambda **kw: _Generation())
    before = _sample("metadata_agent_generations_total", status="partial")

    with TestClient(main.app) as client:
        client.post("/generate", json={"text": "Ein Konzert in Weimar."})

    assert _sample("metadata_agent_generations_total", status="partial") == before + 1


# ------------------------------------------------------------ spec metadata


def test_the_spec_names_the_service_for_end_users():
    # Team 6 builds a product catalogue from title and summary of the spec.
    info = main.app.openapi()["info"]

    assert info["title"] == "Metadata Agent API"
    assert 40 <= len(info["summary"]) <= 300
    assert "Metadaten" in info["summary"]


# ------------------------------------------------------------ occupied port
#
# uvicorn ends the whole process with sys.exit(1) when it cannot bind - the
# public API included, after a log line that claimed the port was up. A taken
# internal port (a second worker, a clash) must be named instead, and the API
# keep running; probes on the internal port then fail visibly.


@pytest.mark.asyncio
async def test_an_occupied_internal_port_is_named():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]

        with pytest.raises(InternalPortUnavailable) as raised:
            await start_internal_server(
                create_internal_app(lambda: _settings()), port, host="127.0.0.1"
            )

    assert str(port) in str(raised.value)
    assert "Worker" in str(raised.value)


def test_the_api_keeps_running_when_the_internal_port_is_taken(monkeypatch, caplog):
    with socket.socket() as taken:
        taken.bind(("0.0.0.0", 0))
        taken.listen()
        port = taken.getsockname()[1]
        monkeypatch.setattr(main, "settings", _settings(internal_port=port))

        with TestClient(main.app) as client:
            assert client.get("/health").status_code == 200

    assert f"Interner Port {port}" in caplog.text
    assert "Internal port:" not in caplog.text
