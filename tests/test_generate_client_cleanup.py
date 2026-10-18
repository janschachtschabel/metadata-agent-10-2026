"""
/generate closes the LLM client it built for a request - on every way out (audit L-06).

A request that picks its own provider or model gets an LLMService of its own,
with its own HTTP client. When the request was cancelled while it waited for
the screenshot (client gone, server shutting down), CancelledError skipped the
close: the connection pool stayed open until garbage collection.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps


class _Service:
    def __init__(self):
        self.closed = 0
        self.llm_service = SimpleNamespace(close=self._close)

    async def _close(self):
        self.closed += 1

    async def generate_metadata(self, **kwargs):
        return {
            "contextName": "default",
            "schemaVersion": "1.8.1",
            "metadataset": "event.json",
            "language": "de",
            "metadata": {"cclom:title": "Konzert"},
            "_origins": {},
            "processing": {"status": "success", "success": True, "warnings": []},
        }


def test_a_cancelled_screenshot_wait_still_closes_the_requests_client(monkeypatch):
    service = _Service()

    async def capture(**kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: service)
    monkeypatch.setattr(
        deps, "get_screenshot_service", lambda: SimpleNamespace(capture=capture)
    )

    with TestClient(main.app) as client:
        with pytest.raises(BaseException):
            client.post(
                "/generate",
                json={
                    "text": "Ein Konzert.",
                    "llm_model": "gpt-6-luna-mini",
                    "screenshot_method": "playwright",
                    "preview_url": "https://example.org/",
                },
            )

    assert service.closed == 1
