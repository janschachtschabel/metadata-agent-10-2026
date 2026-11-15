"""
input_source=node_id: the text comes from the repository (audit 18.10. final).

fetch_from_node_id had no test at all. The repository is replaced by a fake
HTTP client; everything above it - property conversion, the text fallbacks,
the status a caller sees - runs for real.
"""

from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services.input_source_service import InputSourceService

NODE_ID = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"

PROPERTIES = {
    "cclom:title": ["Bruchrechnung"],
    "cclom:general_description": ["Brüche addieren und kürzen."],
    "cclom:general_keyword": ["Mathematik", "Brüche"],
    "ccm:wwwurl": ["https://example.org/brueche"],
    "sys:node-uuid": [NODE_ID],
    "cm:name": ["intern"],
    "cclom:title_DISPLAYNAME": ["ignoriert"],
}


class _Repository:
    """Answers metadata and textContent like edu-sharing, records the calls."""

    def __init__(self, properties: dict, text_content: dict):
        self.properties = properties
        self.text_content = text_content
        self.requested: list[str] = []

    async def get(self, url, headers=None):
        self.requested.append(url)
        request = httpx.Request("GET", url)
        if url.endswith("/textContent"):
            return httpx.Response(200, json=self.text_content, request=request)
        return httpx.Response(
            200, json={"node": {"properties": self.properties}}, request=request
        )

    @asynccontextmanager
    async def stream(self, method, url, headers=None):
        # The text content is read as a stream (audit 14.11., R-N).
        yield await self.get(url, headers=headers)

    async def aclose(self):
        pass


def _service(properties=PROPERTIES, text_content=None) -> InputSourceService:
    service = InputSourceService()
    service.http_client = _Repository(properties, text_content or {})
    return service


async def test_the_stored_full_text_is_the_source():
    service = _service(text_content={"text": "Der Volltext."})

    data = await service.fetch_from_node_id(NODE_ID)

    assert data.text == "Der Volltext."
    assert data.source_url == "https://example.org/brueche"
    assert data.node_id == NODE_ID
    # Repository arrays become scalars; system, cm: and display names stay out.
    assert data.existing_metadata == {
        "cclom:title": "Bruchrechnung",
        "cclom:general_description": "Brüche addieren und kürzen.",
        "cclom:general_keyword": ["Mathematik", "Brüche"],
        "ccm:wwwurl": "https://example.org/brueche",
    }
    assert all(NODE_ID in url for url in service.http_client.requested)


async def test_without_full_text_title_description_and_keywords_stand_in():
    data = await _service(text_content={}).fetch_from_node_id(NODE_ID)

    assert data.text == (
        "Bruchrechnung\n\nBrüche addieren und kürzen.\n\nKeywords: Mathematik, Brüche"
    )


def test_a_node_without_any_text_is_a_422_that_says_so(monkeypatch):
    """
    No crash, and no fault of the repository either: the node holds nothing to
    read (it was a 502 "Failed to fetch input: ValueError"; audit 14.11., S-N).
    """
    empty = _service(properties={"ccm:wwwurl": ["https://example.org/"]})
    monkeypatch.setattr(deps, "get_input_source_service", lambda: empty)

    with TestClient(main.app) as client:
        response = client.post(
            "/generate", json={"input_source": "node_id", "node_id": NODE_ID}
        )

    assert response.status_code == 422, response.text
    assert "keinen Text" in response.json()["detail"]


@pytest.mark.parametrize("keywords", ["Ein Schlagwort", []])
async def test_a_keyword_field_may_be_a_single_value_or_empty(keywords):
    properties = {"cclom:title": ["Titel"], "cclom:general_keyword": keywords}

    data = await _service(properties=properties).fetch_from_node_id(NODE_ID)

    expected = "Titel\n\nKeywords: Ein Schlagwort" if keywords else "Titel"
    assert data.text == expected
