"""
Screenshots as the HTTP endpoints take them: one capture per request, refused
targets answered as client errors, failures without internals, no orphans.
"""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services import screenshot_service as module

NODE_ID = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"
IMAGE = b"\x89PNG\r\n\x1a\nimage-bytes"


def test_a_preview_upload_reuses_the_single_capture(monkeypatch):
    """
    The endpoint used to capture once for the response and a second time for the
    preview upload — two browser runs, and possibly two different images, for one
    request. Since every capture starts a local Chromium, that doubled the cost.
    """
    url = "https://example.org/"
    monkeypatch.setattr(module, "require_public_http_url", AsyncMock(return_value=url))
    service = module.ScreenshotService()
    browser = AsyncMock(
        return_value=module.ScreenshotResult(IMAGE, method="playwright", url=url)
    )
    monkeypatch.setattr(service, "_capture_playwright", browser)
    monkeypatch.setattr(deps, "get_screenshot_service", lambda: service)
    uploads = []

    async def upload_preview(node_id, image, mimetype, **kwargs):
        uploads.append((node_id, image, mimetype))
        return {"success": True}

    async def preview_refusal(node_id):
        return None

    monkeypatch.setattr(
        deps,
        "get_repository_service",
        lambda: SimpleNamespace(
            upload_preview=upload_preview, preview_refusal=preview_refusal
        ),
    )

    with TestClient(main.app) as client:
        response = client.post("/screenshot", json={"url": url, "node_id": NODE_ID})

    assert response.status_code == 200, response.text
    assert response.json()["preview_uploaded"] is True
    browser.assert_awaited_once()
    assert uploads == [(NODE_ID, IMAGE, "image/png")]


def test_an_internal_target_is_a_400_before_any_browser_starts(monkeypatch):
    service = module.ScreenshotService()
    browser = AsyncMock(side_effect=AssertionError("browser must not start"))
    monkeypatch.setattr(service, "_capture_playwright", browser)
    monkeypatch.setattr(deps, "get_screenshot_service", lambda: service)

    with TestClient(main.app) as client:
        response = client.post("/screenshot", json={"url": "http://127.0.0.1/admin"})

    assert response.status_code == 400
    assert "öffentlich" in response.json()["detail"]
    browser.assert_not_awaited()


def test_an_unexpected_failure_does_not_reveal_its_details(monkeypatch):
    url = "https://example.org/"
    monkeypatch.setattr(module, "require_public_http_url", AsyncMock(return_value=url))
    service = module.ScreenshotService()
    monkeypatch.setattr(
        service,
        "_capture_playwright",
        AsyncMock(side_effect=RuntimeError("connect 10.0.0.5:6379 from /srv/app")),
    )
    monkeypatch.setattr(deps, "get_screenshot_service", lambda: service)

    with TestClient(main.app) as client:
        response = client.post("/screenshot", json={"url": url})

    assert response.status_code == 500
    assert "10.0.0.5" not in response.text
    assert "/srv/app" not in response.text


def test_generate_stops_its_screenshot_when_the_extraction_fails(monkeypatch):
    """Only two captures run at once; an orphan would hold a slot for up to 90 s."""
    cancelled = threading.Event()
    capturing = asyncio.Event()

    async def capture(**kwargs):
        capturing.set()
        try:
            await asyncio.Event().wait()  # until cancelled
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def failing_extraction(**kwargs):
        await asyncio.wait_for(capturing.wait(), 2)
        raise RuntimeError("extraction failed")

    monkeypatch.setattr(
        deps, "get_screenshot_service", lambda: SimpleNamespace(capture=capture)
    )
    service = SimpleNamespace(
        generate_metadata=failing_extraction,
        llm_service=SimpleNamespace(close=AsyncMock()),
    )
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kwargs: service)
    body = {
        "text": "A resource",
        "schema_file": "core.json",
        "screenshot_method": "playwright",
        "preview_url": "https://example.org/",
    }

    with TestClient(main.app, raise_server_exceptions=False) as client:
        response = client.post("/generate", json=body)
        assert response.status_code == 500
        assert cancelled.wait(2), "the screenshot task kept running"


def test_upload_stops_its_screenshot_when_the_upload_fails(monkeypatch):
    cancelled = threading.Event()
    capturing = asyncio.Event()

    async def capture(**kwargs):
        capturing.set()
        try:
            await asyncio.Event().wait()  # until cancelled
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def failing_upload(**kwargs):
        await asyncio.wait_for(capturing.wait(), 2)
        raise RuntimeError("repository unreachable")

    monkeypatch.setattr(
        deps, "get_screenshot_service", lambda: SimpleNamespace(capture=capture)
    )
    monkeypatch.setattr(
        deps,
        "get_repository_service",
        lambda: SimpleNamespace(upload_metadata=failing_upload, _auth_header=""),
    )
    body = {
        "metadata": {"cclom:title": "A resource"},
        "preview_url": "https://example.org/",
    }

    with TestClient(main.app, raise_server_exceptions=False) as client:
        response = client.post("/upload", json=body)
        assert response.status_code == 500
        assert cancelled.wait(2), "the screenshot task kept running"


# ------------- release review 2026-10-18 (#10): /screenshot with node_id


def _shot():
    return SimpleNamespace(
        image_bytes=IMAGE,
        mimetype="image/png",
        format="png",
        width=1,
        height=1,
        size_bytes=len(IMAGE),
        capture_time_ms=1,
        method="playwright",
        url="https://example.org/",
    )


def test_a_node_outside_its_window_is_refused_before_the_browser_starts(
    monkeypatch,
):
    captured = []

    async def capture(**kwargs):
        captured.append(kwargs)
        return _shot()

    async def preview_refusal(node_id):
        return {"success": False, "error_code": "node_too_old", "error": "zu alt"}

    async def upload_preview(*args, **kwargs):
        raise AssertionError("no upload for a refused node")

    monkeypatch.setattr(
        deps, "get_screenshot_service", lambda: SimpleNamespace(capture=capture)
    )
    monkeypatch.setattr(
        deps,
        "get_repository_service",
        lambda: SimpleNamespace(
            preview_refusal=preview_refusal, upload_preview=upload_preview
        ),
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/screenshot", json={"url": "https://example.org/", "node_id": NODE_ID}
        )

    assert response.status_code == 403, response.text
    assert captured == [], "a refused node must not hold a browser slot"


def test_a_screenshot_for_a_node_answers_504_at_the_upload_deadline(monkeypatch):
    async def capture(**kwargs):
        await asyncio.sleep(3600)

    async def preview_refusal(node_id):
        return None

    monkeypatch.setattr(
        deps, "get_screenshot_service", lambda: SimpleNamespace(capture=capture)
    )
    monkeypatch.setattr(
        deps,
        "get_repository_service",
        lambda: SimpleNamespace(preview_refusal=preview_refusal),
    )
    monkeypatch.setattr(deps.settings, "upload_timeout", 0.2)

    with TestClient(main.app) as client:
        response = client.post(
            "/screenshot", json={"url": "https://example.org/", "node_id": NODE_ID}
        )

    assert response.status_code == 504, response.text
    assert response.json()["error"]["code"] == "UPLOAD_TIMEOUT"
