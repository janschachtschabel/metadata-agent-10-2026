"""
/generate gives up as a whole before the ingress does (audit P-01).

Each LLM call has its own timeout, but a run of many fields, retries and
geocoding had no upper bound. The ingress cut it off after 120 s with a bare
504 from the proxy - no request id, no CORS, and the field calls kept running
and spending tokens for an answer nobody would receive.
"""

import asyncio

from fastapi.testclient import TestClient

from src import main
from src.api import deps


class _SlowService:
    """Fans out like MetadataService.extract_fields_parallel (asyncio.gather)."""

    def __init__(self, seconds=10.0, fields=3):
        self.seconds, self.fields = seconds, fields
        self.cancelled = 0
        self.llm_service = None

    async def _field(self):
        try:
            await asyncio.sleep(self.seconds)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return "x"

    async def generate_metadata(self, **kwargs):
        await asyncio.gather(*(self._field() for _ in range(self.fields)))
        return {
            "contextName": "default",
            "schemaVersion": "1.8.1",
            "metadataset": "event.json",
            "language": "de",
            "exportedAt": "2026-10-17T00:00:00Z",
            "metadata": {"cclom:title": "Kurs"},
            "processing": {"status": "success", "success": True, "warnings": []},
        }


class _HangingInput:
    """A fetch that only ends when cancelled - or, if nothing cancels it, after 5 s."""

    def __init__(self):
        self.cancelled = False

    async def fetch_from_url(self, url, method=None, **kwargs):
        try:
            await asyncio.wait_for(asyncio.Event().wait(), 5)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        except TimeoutError:
            pass
        return "Seitentext."


def test_a_run_over_the_limit_is_a_504_and_stops_working(monkeypatch):
    service = _SlowService()
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: service)
    monkeypatch.setattr(deps.settings, "generate_timeout", 0.05)

    with TestClient(main.app) as client:
        response = client.post("/generate", json={"text": "Ein Kurs über Brüche."})

    assert response.status_code == 504, response.text
    body = response.json()
    assert body["error"]["code"] == "GENERATION_TIMEOUT"
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    # Every field call stopped, not only the run awaiting them.
    assert service.cancelled == 3


def test_the_limit_includes_fetching_the_source(monkeypatch):
    # Outside the limit the fetch would end after 5 s and the instant
    # generation would answer 200.
    service = _SlowService(seconds=0)
    source = _HangingInput()
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: service)
    monkeypatch.setattr(deps, "get_input_source_service", lambda: source)
    monkeypatch.setattr(deps.settings, "generate_timeout", 0.05)

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={"input_source": "url", "source_url": "https://example.org/kurs"},
        )

    assert response.status_code == 504, response.text
    assert source.cancelled


def test_the_default_limit_stays_below_the_ingress_timeout():
    # Ingress annotation proxy-read-timeout: 120 (helm/metadata-agent/values.yaml).
    assert deps.settings.request_timeout < deps.settings.generate_timeout < 120
