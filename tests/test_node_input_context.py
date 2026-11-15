"""
A node as input is read with the request's context and version, and what the
read has to say reaches the caller (audit 14.11., follow-up of R-06 and R-N in
the API layer).

The input service keeps only the properties a schema of the context declares,
so it has to know which context the request means - it was asked without one.
Its warnings (a stored full text that had to be cut) were passed on for
`input_source=node_url` and dropped for `node_id`.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services.input_source_service import InputData

NODE = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"
WARNING = "Gespeicherter Volltext auf 150.000 Zeichen gekürzt."
RESULT = {
    "contextName": "mds_oeh",
    "schemaVersion": "2.0.0",
    "metadataset": "event.json",
    "language": "de",
    "exportedAt": "2026-11-14T00:00:00Z",
    "metadata": {"cclom:title": "Kurs"},
    "processing": {"status": "success", "success": True, "warnings": []},
}


class _Node:
    def __init__(self):
        self.reads = []

    async def _read(self, node_id, **kwargs):
        self.reads.append(kwargs)
        return InputData(
            text="Ein Text über Bruchrechnung.",
            existing_metadata={"cclom:title": "Bruchrechnung"},
            warnings=[WARNING],
        )

    fetch_from_node_id = fetch_from_node_url = _read


class _Services:
    """Metadata and LLM service in one: every call answers at once."""

    llm_service = None
    provider = model = "fake"

    async def generate_metadata(self, **kwargs):
        return {**RESULT, "processing": dict(RESULT["processing"])}

    async def detect_schema(self, text, context, version, language):
        return {"schema_file": "event.json", "confidence": "high", "method": "llm"}

    async def extract_field(self, field, text, existing_value, language):
        return field["id"], "Titel", None

    async def close(self):
        pass


@pytest.fixture
def node(monkeypatch):
    node = _Node()
    monkeypatch.setattr(deps, "get_input_source_service", lambda: node)
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: _Services())
    monkeypatch.setattr(deps, "get_llm_service", lambda **kw: _Services())
    return node


ENDPOINTS = [
    ("/generate", {}, lambda answer: answer["processing"]["warnings"]),
    ("/detect-content-type", {}, lambda answer: answer["warnings"]),
    (
        "/extract-field",
        {"schema_file": "event.json", "field_id": "cclom:title"},
        lambda answer: answer["processing"]["warnings"],
    ),
]


@pytest.mark.parametrize("path, extra, warnings", ENDPOINTS)
@pytest.mark.parametrize("source", ["node_id", "node_url"])
def test_the_node_is_read_with_context_and_version_of_the_request(
    node, path, extra, warnings, source
):
    body = {
        "input_source": source,
        "node_id": NODE,
        "context": "mds_oeh",
        "version": "1.8.1",
        **extra,
    }

    with TestClient(main.app) as client:
        response = client.post(path, json=body)

    assert response.status_code == 200, response.text
    (read,) = node.reads
    assert (read["context"], read["version"]) == ("mds_oeh", "1.8.1")
    # What the read reports is in the answer, for node_id as for node_url.
    assert WARNING in warnings(response.json())


def test_without_them_the_defaults_of_the_request_are_passed(node):
    with TestClient(main.app) as client:
        response = client.post(
            "/generate", json={"input_source": "node_id", "node_id": NODE}
        )

    assert response.status_code == 200, response.text
    assert node.reads == [{"context": "default", "version": "latest"}]
