"""
The header of a /generate answer is no metadata (audit 14.11., L-33 / K-03).

The widget sends the last answer back as `existing_metadata`. Its header keys
(`contextName`, `schemaVersion`, `metadataset`, `metadataset_uri`, `language`,
`exportedAt`, `processing`, `preview_image_url`) were merged like fields: round
2 with another content type and language answered with the header of round 1,
and the old `processing` block stood in the correction prompt. They are dropped
before the merge now - from the body and from what a node delivers - and no
metadata key replaces a header key of the answer.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.api.input_resolver import HEADER_KEYS, without_header
from src.api.routers.generate import _flat_response
from src.services.input_source_service import InputData

from test_generate_failures import EVENT_TEXT, _Gateway, stack  # noqa: F401 - fixture


def _prompts(gateway) -> str:
    return "\n".join(request.content.decode("utf-8") for request in gateway.calls)


def test_a_whole_answer_as_existing_metadata_keeps_the_new_header(stack):  # noqa: F811
    gateway = _Gateway(content='{"value": "Kurs"}')
    stack(gateway)

    with TestClient(main.app) as client:
        first = client.post(
            "/generate",
            json={
                "text": EVENT_TEXT,
                "schema_file": "event.json",
                "language": "de",
                "enable_geocoding": False,
            },
        )
        assert first.status_code == 200, first.text
        answer = first.json()
        assert answer["metadataset"] == "event.json"
        gateway.calls.clear()

        second = client.post(
            "/generate",
            json={
                "text": "Titel kürzer",
                "existing_metadata": answer,
                "schema_file": "person.json",
                "language": "en",
                "enable_geocoding": False,
            },
        )

    assert second.status_code == 200, second.text
    again = second.json()
    assert again["metadataset"] == "person.json"
    assert again["metadataset_uri"].endswith("/person")
    assert again["language"] == "en"
    assert again["exportedAt"] != answer["exportedAt"]
    assert again["processing"]["llm_model"]
    # The correction prompt lists the fields of round 1, not its header.
    prompts = _prompts(gateway)
    assert "BISHERIGER STAND" in prompts
    assert "cclom:title: Kurs" in prompts
    for key in HEADER_KEYS:
        assert f"  {key}:" not in prompts, key
    assert "fields_extracted" not in prompts


class _Node:
    """A node whose stored record carries the header of an earlier answer."""

    async def fetch_from_node_id(self, node_id, **kwargs):
        return InputData(
            text="Ein Text über Bruchrechnung.",
            existing_metadata={
                "cclom:title": "Bruchrechnung",
                "metadataset": "event.json",
                "language": "de",
                "exportedAt": "2020-01-01T00:00:00Z",
                "processing": {"status": "success"},
            },
        )


def test_header_keys_a_node_delivers_are_dropped_too(stack, monkeypatch):  # noqa: F811
    stack(_Gateway(content='{"value": "Kurs"}'))
    monkeypatch.setattr(deps, "get_input_source_service", lambda: _Node())

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={
                "input_source": "node_id",
                "node_id": "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9",
                "schema_file": "person.json",
                "language": "en",
                "enable_geocoding": False,
            },
        )

    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["metadataset"] == "person.json"
    assert answer["language"] == "en"
    assert answer["exportedAt"] != "2020-01-01T00:00:00Z"
    assert answer["processing"]["status"] != {"status": "success"}
    assert answer["cclom:title"]


def test_without_header_keeps_fields_and_internal_keys():
    record = {key: "x" for key in HEADER_KEYS}
    record.update({"cclom:title": "T", "_origins": {"cclom:title": "user"}})

    assert without_header(record) == {
        "cclom:title": "T",
        "_origins": {"cclom:title": "user"},
    }


@pytest.mark.parametrize("key", HEADER_KEYS)
def test_no_metadata_key_replaces_a_header_key_of_the_answer(key):
    result = {
        "contextName": "default",
        "schemaVersion": "2.0.0",
        "metadataset": "person.json",
        "language": "en",
        "exportedAt": "2026-11-14T00:00:00Z",
        "metadata": {key: "aus den Metadaten", "cclom:title": "T"},
        "processing": {"status": "success"},
    }

    response = _flat_response(result, "uri:person", "Quelle", None)

    assert response.get(key) != "aus den Metadaten"
    assert response["cclom:title"] == "T"
    assert response["metadataset_uri"] == "uri:person"
