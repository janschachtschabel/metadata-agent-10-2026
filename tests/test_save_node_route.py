"""
A successful POST /nodes, end to end against a fake repository (audit 18.10. final).

No test reached a successful /nodes before: the route built its answer field by
field from the service result and nothing pinned that answer. These run the
real RepositoryService against a recording stand-in for httpx.AsyncClient, so
they cover the body forms, the workflow switches and the response keys - and
pin them before the response mapping is shared with /upload.
"""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services import repository_service as repo_module
from src.services.repository_curation import (
    DEFAULT_WORKFLOW_STATUS,
    QUALITY_WORKFLOW_STATUS,
)
from src.services.repository_service import RepositoryService

NEW_ID = "11111111-2222-4333-8444-555555555555"
OLD_ID = "177fba93-3885-479e-bfba-933885c79e1c"
BASE = "https://repository.staging.openeduhub.net/edu-sharing"

METADATA = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
    "cclom:title": "Bruchrechnung Klasse 6",
    "cclom:general_description": "Arbeitsblatt zur Bruchrechnung.",
    "ccm:wwwurl": "https://example.org/bruchrechnung",
    "cclom:general_keyword": ["Bruchrechnung", "Mathematik"],
}


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class _Repository:
    """Stands in for httpx.AsyncClient: a young node, every write accepted."""

    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None, files=None):
        type(self).calls.append({"method": "POST", "url": url, "json": json})
        if "/children?" in url:
            return _Response(200, {"node": {"ref": {"id": NEW_ID}}})
        return _Response(200, {})

    async def put(self, url, headers=None, json=None):
        type(self).calls.append({"method": "PUT", "url": url, "json": json})
        return _Response(200, {})

    async def get(self, url, headers=None):
        type(self).calls.append({"method": "GET", "url": url, "json": None})
        node_id = url.split("/nodes/-home-/")[1].split("/")[0]
        return _Response(
            200,
            {
                "node": {
                    "ref": {"id": node_id},
                    "properties": {"cclom:title": ["Bruchrechnung Klasse 6"]},
                    "aspects": [],
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    async def delete(self, url, headers=None):
        type(self).calls.append({"method": "DELETE", "url": url, "json": None})
        return _Response(200, {})


@pytest.fixture
def client(monkeypatch):
    _Repository.calls = []
    monkeypatch.setattr(repo_module.httpx, "AsyncClient", _Repository)
    monkeypatch.setattr(repo_module.httpx, "Timeout", lambda *a, **k: None)
    monkeypatch.setattr(
        repo_module,
        "get_repository_config",
        lambda: {"base_url": BASE, "inbox_id": "inbox"},
    )
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )
    with TestClient(main.app) as test_client:
        test_client.calls = _Repository.calls
        yield test_client


def _metadata_write(calls):
    writes = [
        c for c in calls if c["method"] == "POST" and "METADATA_UPDATE" in c["url"]
    ]
    assert len(writes) == 1, calls
    return writes[0]


def _workflow_states(calls):
    return [
        c["json"]["status"]
        for c in calls
        if c["method"] == "PUT" and c["url"].endswith("/workflow")
    ]


def test_creating_a_node_answers_with_the_written_node(client):
    response = client.post("/nodes", json=METADATA)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["node_full"]["ref"]["id"] == NEW_ID
    body.pop("node_full")
    assert body == {
        "success": True,
        "node": {
            "nodeId": NEW_ID,
            "title": "Bruchrechnung Klasse 6",
            "description": "Arbeitsblatt zur Bruchrechnung.",
            "wwwurl": "https://example.org/bruchrechnung",
            "repositoryUrl": f"{BASE}/components/render/{NEW_ID}",
        },
        "node_created": True,
        "error": None,
        "step": None,
        "fields_written": body["fields_written"],
        "fields_skipped": 0,
        "skipped_fields": [],
        "schema_used": "learning_material.json",
        "repo_fields_available": body["repo_fields_available"],
        "field_errors": None,
        "preview": None,
        "collections": None,
        "workflow": None,
        "discarded_node": None,
    }
    assert body["fields_written"] >= 4
    assert body["repo_fields_available"] > 20
    assert [c for c in client.calls if "/children?" in c["url"]], "node created"
    assert _workflow_states(client.calls) == [], "workflows are off by default"


def test_updating_a_node_writes_into_it_and_creates_nothing(client):
    response = client.post(
        "/nodes",
        json={
            "metadataset": "learning_material.json",
            "node_id": OLD_ID,
            "cclom:general_keyword": ["Kernphysik"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["node_created"] is False
    assert body["node"]["nodeId"] == OLD_ID
    assert body["node_full"]["ref"]["id"] == OLD_ID
    assert not [c for c in client.calls if "/children?" in c["url"]]
    write = _metadata_write(client.calls)
    assert f"/nodes/-home-/{OLD_ID}/metadata" in write["url"]
    assert write["json"] == {"cclom:general_keyword": ["Kernphysik"]}


@pytest.mark.parametrize(
    "switches, states",
    [
        ({"start_review_workflow": True}, [DEFAULT_WORKFLOW_STATUS]),
        ({"start_quality_workflow": True}, [QUALITY_WORKFLOW_STATUS]),
        (
            {"start_review_workflow": True, "start_quality_workflow": True},
            [DEFAULT_WORKFLOW_STATUS, QUALITY_WORKFLOW_STATUS],
        ),
    ],
)
def test_the_workflow_switches_run_their_states_in_order(client, switches, states):
    response = client.post("/nodes", json={**METADATA, **switches})

    assert response.status_code == 200, response.text
    assert _workflow_states(client.calls) == states
    assert response.json()["workflow"] == [
        {"status": state, "success": True, "error": None, "skipped": None}
        for state in states
    ]


def test_the_nested_metadata_body_writes_the_same_fields(client):
    flat = client.post("/nodes", json=METADATA).json()
    flat_write = _metadata_write(client.calls)["json"]
    client.calls.clear()

    markers = {k: METADATA[k] for k in ("contextName", "schemaVersion", "metadataset")}
    fields = {k: v for k, v in METADATA.items() if k not in markers}
    nested = client.post(
        "/nodes", json={**markers, "metadata": fields, "collection_id": []}
    ).json()

    assert _metadata_write(client.calls)["json"] == flat_write
    assert nested["schema_used"] == flat["schema_used"] == "learning_material.json"
    assert nested["fields_written"] == flat["fields_written"]
    assert nested["node"] == flat["node"]
