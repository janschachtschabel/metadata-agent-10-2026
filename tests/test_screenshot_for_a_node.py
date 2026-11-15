"""
/screenshot with `node_id` needs the repository like every write path (audit
14.11., R-10).

Without repository credentials the endpoint captured the page and answered 200
`success: true`, where /upload, /nodes, /node and /workflow answer 503 - a
browser run for an image nobody could store. Now 503 before the capture.
`success` says that the capture succeeded; whether the image reached the node
is `preview_uploaded`, and a refusal of the repository stands in `error`.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.models.schemas import ScreenshotResponse
from src.services import screenshot_service as module

NODE_ID = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"
URL = "https://example.org/"
IMAGE = b"\x89PNG\r\n\x1a\nimage-bytes"


@pytest.fixture
def browser(monkeypatch):
    monkeypatch.setattr(module, "require_public_http_url", AsyncMock(return_value=URL))
    service = module.ScreenshotService()
    capture = AsyncMock(
        return_value=module.ScreenshotResult(IMAGE, method="playwright", url=URL)
    )
    monkeypatch.setattr(service, "_capture_playwright", capture)
    monkeypatch.setattr(deps, "get_screenshot_service", lambda: service)
    return capture


def test_without_repository_credentials_it_is_a_503_and_no_capture(
    browser, monkeypatch
):
    monkeypatch.setattr(deps, "get_repository_service", lambda: None)

    with TestClient(main.app) as client:
        response = client.post("/screenshot", json={"url": URL, "node_id": NODE_ID})

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    browser.assert_not_awaited()


def test_without_a_node_no_repository_is_needed(browser, monkeypatch):
    monkeypatch.setattr(deps, "get_repository_service", lambda: None)

    with TestClient(main.app) as client:
        response = client.post("/screenshot", json={"url": URL})

    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["success"] is True
    assert answer["preview_uploaded"] is None
    assert answer["error"] is None


@pytest.mark.parametrize(
    "refusal, error",
    [
        ({"success": False, "error": "Repository: HTTP 500"}, "Repository: HTTP 500"),
        ({"success": False}, "Vorschaubild"),
    ],
)
def test_a_refused_preview_upload_is_a_capture_that_succeeded(
    browser, monkeypatch, refusal, error
):
    async def upload_preview(node_id, image, mimetype, **kwargs):
        return refusal

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
        response = client.post("/screenshot", json={"url": URL, "node_id": NODE_ID})

    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["success"] is True
    assert answer["image_base64"]
    assert answer["preview_uploaded"] is False
    assert error in answer["error"]
    assert answer["node_id"] == NODE_ID


def test_the_schema_says_what_success_means():
    fields = ScreenshotResponse.model_fields

    assert "preview_uploaded" in fields["success"].description
    assert fields["error"].description
