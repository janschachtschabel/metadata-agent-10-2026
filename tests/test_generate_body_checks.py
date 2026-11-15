"""
What /generate refuses before any source is fetched or model is asked.

Audit 13.11., L-19: `_origins` as a list, `_source_text` as a number and NaN in
the body all ended as 500 INTERNAL_ERROR - the NaN only after the complete,
paid LLM run, when the answer could not be written as JSON.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps


class _MustNotRun:
    llm_service = None

    async def generate_metadata(self, **kwargs):
        raise AssertionError("generation started")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: _MustNotRun())
    with TestClient(main.app) as client:
        yield client


@pytest.mark.parametrize(
    "existing",
    [
        {"_origins": []},
        {"_origins": "ai"},
        {"_origins": {"cclom:title": 5}},
        {"_source_text": 123},
        {"_source_text": ["Text"]},
    ],
)
def test_internal_keys_of_the_wrong_kind_are_a_422(client, existing):
    response = client.post(
        "/generate", json={"text": "Ein Text", "existing_metadata": existing}
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_a_body_with_nan_is_no_json(client, constant):
    body = '{"text": "Ein Text", "existing_metadata": {"schema:price": %s}}' % constant

    response = client.post(
        "/generate", content=body, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 400, response.text
    assert constant.lstrip("-") in response.json()["detail"]


def test_a_text_with_line_breaks_is_still_repaired(monkeypatch):
    """The lenient parsing of pasted multi-line texts is unchanged."""
    seen = {}

    class _Echo:
        llm_service = None

        async def generate_metadata(self, **kwargs):
            seen.update(kwargs)
            return {
                "contextName": "default",
                "schemaVersion": "2.0.0",
                "metadataset": "event.json",
                "language": "de",
                "exportedAt": "2026-11-13T00:00:00Z",
                "metadata": {"cclom:title": "Kurs"},
                "processing": {"status": "success", "success": True, "warnings": []},
            }

    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: _Echo())
    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            content='{"text": "Zeile eins\nZeile zwei"}'.encode(),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200, response.text
    assert "Zeile zwei" in seen["text"]
