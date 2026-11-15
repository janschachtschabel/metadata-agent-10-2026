"""
"This node holds nothing to read" has an exception of its own (audit
14.11.2026, list S-N; request of the API strand).

The input service said it with a bare ValueError, and the API layer told it
from a fault by `type(e) is ValueError`: any ValueError raised anywhere below -
int("x") in a helper, a library - would have been answered as 422 "the node
has no text" instead of 502. The service raises NodeWithoutText, and only that
is the 422.
"""

from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services.input_source_service import (
    InputSourceService,
    NodeWithoutText,
    TextExtractionError,
)

NODE_ID = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"


class _Repository:
    """A node with the given properties and no stored text."""

    def __init__(self, properties: dict):
        self.properties = properties

    async def get(self, url, headers=None):
        request = httpx.Request("GET", url)
        if url.endswith("/textContent"):
            return httpx.Response(200, json={}, request=request)
        return httpx.Response(
            200, json={"node": {"properties": self.properties}}, request=request
        )

    @asynccontextmanager
    async def stream(self, method, url, headers=None):
        yield await self.get(url, headers=headers)

    async def aclose(self):
        pass


def _service(properties: dict) -> InputSourceService:
    service = InputSourceService()
    service.http_client = _Repository(properties)
    return service


async def test_a_node_without_text_title_description_or_keywords():
    service = _service({"ccm:wwwurl": ["https://example.org/"]})

    with pytest.raises(NodeWithoutText):
        await service.fetch_from_node_id(NODE_ID)


async def test_a_node_without_stored_text_and_without_a_url():
    service = _service({"cclom:title": ["Titel"]})

    with pytest.raises(NodeWithoutText):
        await service.fetch_from_node_url(NODE_ID)


async def test_a_node_whose_page_cannot_be_read_and_that_describes_nothing(
    monkeypatch,
):
    service = _service({"ccm:wwwurl": ["https://example.org/"]})

    async def unreachable(url, *args, **kwargs):
        raise TextExtractionError("TEXT_EXTRACTION_TIMEOUT", "zu langsam", 504)

    monkeypatch.setattr(service, "fetch_from_url", unreachable)

    with pytest.raises(NodeWithoutText):
        await service.fetch_from_node_url(NODE_ID)


def test_it_is_no_value_error():
    """Or `except ValueError` somewhere above would take it for a fault of its own."""
    assert not issubclass(NodeWithoutText, ValueError)


class _Faulty:
    """A fault inside the input service that happens to be a ValueError."""

    async def fetch_from_node_id(self, node_id, **kwargs):
        raise ValueError("invalid literal for int() with base 10: 'x'")

    fetch_from_node_url = fetch_from_node_id


@pytest.mark.parametrize("source", ["node_id", "node_url"])
def test_a_value_error_of_the_service_is_a_fault_not_an_empty_node(monkeypatch, source):
    monkeypatch.setattr(deps, "get_input_source_service", lambda: _Faulty())

    with TestClient(main.app) as client:
        response = client.post(
            "/generate", json={"input_source": source, "node_id": NODE_ID}
        )

    assert response.status_code == 502, response.text
    assert "keinen Text" not in response.text
