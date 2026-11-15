"""
The screenshot of /generate: which values ask for one, and the answer with one
(audit 14.11., S-40 and Q-12).

`screenshot_method` was free text: every non-empty value - "none", "off",
"false" - started a capture. It is the enum of /screenshot and /upload now,
blank meaning "not set". The way that ends with `preview_image_url` in the
answer ran in no test.
"""

import base64

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src import main
from src.api import deps
from src.models.schemas import GenerateRequest, ScreenshotMethod

IMAGE = b"\x89PNG\r\n\x1a\nimage-bytes"
RESULT = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "event.json",
    "language": "de",
    "exportedAt": "2026-11-14T00:00:00Z",
    "metadata": {"cclom:title": "Kurs", "ccm:wwwurl": "https://example.org/kurs"},
    "processing": {"status": "success", "success": True, "warnings": []},
}


class _Generator:
    llm_service = None

    async def generate_metadata(self, **kwargs):
        return {**RESULT, "metadata": dict(RESULT["metadata"])}


class _Shot:
    image_bytes, format, capture_time_ms = IMAGE, "png", 12


class _Camera:
    def __init__(self):
        self.calls = []

    async def capture(self, **kwargs):
        self.calls.append(kwargs)
        return _Shot()


@pytest.fixture
def camera(monkeypatch):
    camera = _Camera()
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: _Generator())
    monkeypatch.setattr(deps, "get_screenshot_service", lambda: camera)
    return camera


def _generate(**body):
    with TestClient(main.app) as client:
        return client.post("/generate", json={"text": "Ein Text", **body})


# ------------------------------------------------------------ S-40


@pytest.mark.parametrize("value", ["none", "off", "false", "Playwright", "x" * 1000])
def test_anything_but_the_two_methods_is_refused(camera, value):
    response = _generate(screenshot_method=value, preview_url="https://example.org/")

    assert response.status_code == 422, response.text
    assert "screenshot_method" in response.json()["detail"]
    assert camera.calls == []


@pytest.mark.parametrize("value", ["", "  ", None])
def test_blank_is_not_set_and_takes_no_screenshot(camera, value):
    response = _generate(screenshot_method=value, preview_url="https://example.org/")

    assert response.status_code == 200, response.text
    assert "preview_image_url" not in response.json()
    assert camera.calls == []


def test_the_model_holds_the_enum():
    assert GenerateRequest(text="x").screenshot_method is None
    request = GenerateRequest(text="x", screenshot_method="pageshot")
    assert request.screenshot_method is ScreenshotMethod.PAGESHOT
    with pytest.raises(ValidationError):
        GenerateRequest(text="x", screenshot_method="none")


def test_the_spec_names_the_two_methods():
    schemas = main.app.openapi()["components"]["schemas"]
    described = str(schemas["GenerateRequest"]["properties"]["screenshot_method"])

    assert "#/components/schemas/ScreenshotMethod" in described
    assert schemas["ScreenshotMethod"]["enum"] == ["pageshot", "playwright"]


# ------------------------------------------------------------ Q-12


@pytest.mark.parametrize("method", ["playwright", "pageshot"])
def test_a_capture_comes_back_as_data_url_next_to_the_metadata(camera, method):
    response = _generate(
        screenshot_method=method, preview_url="https://example.org/vorschau"
    )

    assert response.status_code == 200, response.text
    answer = response.json()
    expected = "data:image/png;base64," + base64.b64encode(IMAGE).decode("ascii")
    assert answer["preview_image_url"] == expected
    assert answer["cclom:title"] == "Kurs"
    assert list(answer)[-1] == "processing"
    assert camera.calls == [{"url": "https://example.org/vorschau", "method": method}]


@pytest.mark.parametrize(
    "body, captured",
    [
        (
            {"input_source": "url", "source_url": "https://example.org/quelle"},
            "https://example.org/quelle",
        ),
        (
            {"existing_metadata": {"ccm:wwwurl": ["https://example.org/aus-feld"]}},
            "https://example.org/aus-feld",
        ),
    ],
)
def test_without_a_preview_url_the_source_or_the_record_names_the_page(
    camera, monkeypatch, body, captured
):
    class _Page:
        async def fetch_from_url(self, *args, **kwargs):
            return "Seitentext"

    monkeypatch.setattr(deps, "get_input_source_service", lambda: _Page())

    response = _generate(screenshot_method="playwright", **body)

    assert response.status_code == 200, response.text
    assert response.json()["preview_image_url"].startswith("data:image/png;base64,")
    assert [call["url"] for call in camera.calls] == [captured]


def test_no_url_anywhere_is_no_capture(camera):
    response = _generate(screenshot_method="playwright")

    assert response.status_code == 200, response.text
    assert "preview_image_url" not in response.json()
    assert camera.calls == []
