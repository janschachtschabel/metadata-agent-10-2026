"""
A failed run with a failed capture logs no "Task exception was never retrieved".

The review of 2026-10-18 suspected release() of leaving the capture's
exception unretrieved. It does not: Future.cancel() clears the log flag
before it checks the state, also on a finished task (CPython 3.13). This
pins that release() keeps calling cancel() on every way out.
"""

import asyncio
import gc
import logging

from fastapi.testclient import TestClient

from src import main
from src.api import deps


class _FailingCapture:
    async def capture(self, url, method):
        raise RuntimeError("browser crashed")


class _FailedRun:
    llm_service = None

    async def generate_metadata(self, **kwargs):
        await asyncio.sleep(0.05)  # the capture fails meanwhile
        return {
            "contextName": "default",
            "schemaVersion": "2.0.0",
            "metadataset": "event.json",
            "language": "de",
            "exportedAt": "2026-10-18T00:00:00Z",
            "metadata": {},
            "processing": {
                "status": "failed",
                "success": False,
                "errors": ["x"],
                "warnings": [],
                "error": {"code": "EXTRACTION_FAILED", "message": "nichts"},
            },
        }


def test_a_failed_capture_leaves_no_unretrieved_exception(monkeypatch, caplog):
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: _FailedRun())
    monkeypatch.setattr(deps, "get_screenshot_service", lambda: _FailingCapture())

    with caplog.at_level(logging.ERROR, logger="asyncio"):
        with TestClient(main.app) as client:
            response = client.post(
                "/generate",
                json={
                    "text": "Ein Kurs.",
                    "screenshot_method": "playwright",
                    "preview_url": "https://example.org/",
                },
            )
        gc.collect()

    assert response.status_code != 200
    assert not [r for r in caplog.records if "never retrieved" in r.getMessage()]
