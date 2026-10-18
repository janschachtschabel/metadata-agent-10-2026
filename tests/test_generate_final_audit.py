"""
/generate findings of the final pre-release audit (2026-10-18).

- API-06: an unknown context, version or schema file was answered with 502
  GENERATION_FAILED - after fetching the source, starting the screenshot and
  (schema_file auto) an LLM call. /extract-field answers 404 for the same input.
- API-10: a refused body came back as pydantic's text with the input values and
  documentation links; every 422 now has the form of invalid_body.
- S-17: the screenshot log line carried the caller's full URL (query tokens).
- Compatibility: the 2.1.1 widget sends _source_text with every run; a pasted
  new text over the correction limit was refused with 400 instead of extracted.
"""

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api.routers import generate as generate_module

from test_generate_failures import _Gateway, stack  # noqa: F401 - fixture


@pytest.fixture
def no_fetch(monkeypatch):
    """resolve_input must not run for a request refused up front."""

    async def refuse(req):
        raise AssertionError("the source was fetched")

    monkeypatch.setattr(generate_module, "resolve_input", refuse)


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"context": "gibt-es-nicht"}, "gibt-es-nicht"),
        ({"version": "9.9.9"}, "9.9.9"),
        ({"schema_file": "fehlt.json"}, "fehlt.json"),
        ({"schema_file": "../core.json"}, "core.json"),
        ({"version": "9.9.9", "schema_file": "event.json"}, "9.9.9"),
    ],
)
def test_an_unknown_schema_is_a_404_before_the_source_is_fetched(
    no_fetch, body, fragment
):
    client = TestClient(main.app, raise_server_exceptions=False)

    response = client.post(
        "/generate",
        json={"source_url": "https://example.org/x", "input_source": "url", **body},
    )

    assert response.status_code == 404, response.text
    assert fragment in response.json()["detail"]


def test_a_known_schema_still_reaches_the_source(no_fetch):
    client = TestClient(main.app, raise_server_exceptions=False)

    response = client.post(
        "/generate",
        json={
            "source_url": "https://example.org/x",
            "input_source": "url",
            "schema_file": "event.json",
            "version": "v2.0.0",
        },
    )

    assert response.status_code == 500  # the AssertionError of no_fetch


def test_a_refused_body_is_a_422_without_input_values_or_links():
    client = TestClient(main.app, raise_server_exceptions=False)

    response = client.post(
        "/generate", json={"text": "Ein Text.", "max_workers": "geheim-123"}
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail.startswith("Ungültiger Request-Body: max_workers:")
    assert "geheim-123" not in detail
    assert "pydantic" not in detail


def test_the_screenshot_log_line_has_no_query(monkeypatch, caplog):
    class _Capture:
        async def capture(self, url, method):
            return None

    monkeypatch.setattr(
        generate_module.deps, "get_screenshot_service", lambda: _Capture()
    )
    req = generate_module.GenerateRequest(
        text="x",
        source_url="https://user:pw@example.org/page?token=geheim",
        screenshot_method="playwright",
    )

    async def run():
        task = generate_module._start_screenshot(req, {})
        await task

    with caplog.at_level(logging.INFO, logger=generate_module.__name__):
        asyncio.run(run())

    assert "https://example.org/page" in caplog.text
    assert "geheim" not in caplog.text
    assert "pw@" not in caplog.text


# ------------------- compatibility: a long text with _source_text is a new source


def test_a_long_new_text_with_an_old_source_text_is_a_new_source(stack):  # noqa: F811
    gateway = _Gateway(content='{"value": "Neuer Titel"}')
    stack(gateway)
    new_text = "Ein ganz neuer Text über Kammermusik in Weimar. " * 250  # > 10,000

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={
                "text": new_text,
                "schema_file": "event.json",
                "enable_geocoding": False,
                "existing_metadata": {
                    "cclom:title": "Alter Titel",
                    "_source_text": "Alte Quelle über Orgelmusik.",
                },
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["_source_text"].startswith("Ein ganz neuer Text")
    assert any("neue Quelle" in w for w in body["processing"]["warnings"])
    sent = " ".join(call.content.decode("utf-8") for call in gateway.calls)
    assert "Alte Quelle" not in sent
    assert "BISHERIGER STAND" not in sent


def test_a_short_correction_still_corrects(stack):  # noqa: F811
    gateway = _Gateway(content='{"value": "Kürzerer Titel"}')
    stack(gateway)

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={
                "text": "Titel kürzer",
                "schema_file": "event.json",
                "enable_geocoding": False,
                "existing_metadata": {
                    "cclom:title": "Alter Titel",
                    "_source_text": "Alte Quelle über Orgelmusik.",
                },
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["_source_text"] == "Alte Quelle über Orgelmusik."
    sent = " ".join(call.content.decode("utf-8") for call in gateway.calls)
    assert "Alte Quelle" in sent
