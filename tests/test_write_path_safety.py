"""
What the repository write path must not do when one of its steps fails.

Each step of an upload changes something editors see: a node in the inbox, a
reference in a collection, an entry in the review queue. A later step must not
run on top of an earlier one that failed, and a partial failure has to show up
in the answer instead of only in the server log.
"""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.models.schemas import UploadRequest
from src.api import deps
from src.services import repository_service as repo_module
from src.services.repository_curation import (
    DEFAULT_WORKFLOW_STATUS,
    run_workflow_steps,
    set_collections,
)
from src.services.repository_service import RepositoryService

NODE_ID = "5ab4b434-4832-45ca-b4b4-34483265ca5d"
OLD_NODE_ID = "11111111-2222-3333-4444-555555555555"
COLLECTION_ID = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"

METADATA = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
    "cclom:title": "Bruchrechnung Klasse 6",
    "cclom:general_description": "Arbeitsblatt zur Bruchrechnung.",
    "ccm:wwwurl": "https://example.org/bruchrechnung",
    "ccm:collection_id": [COLLECTION_ID],
}


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class _Client:
    """Records every call; the class attributes steer what the repository says."""

    calls: list[dict] = []
    metadata_status = 200
    extended_status = 200
    aspect_status = 200
    collection_status = 200
    created = ""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _record(self, method, url, body=None):
        type(self).calls.append({"method": method, "url": url, "json": body})

    async def post(self, url, headers=None, json=None, files=None):
        self._record("POST", url, json)
        if "/children?" in url:
            return _Response(200, {"node": {"ref": {"id": NODE_ID}}})
        if "EXTENDED_DATA" in url:
            return _Response(type(self).extended_status, {})
        if "METADATA_UPDATE" in url:
            return _Response(type(self).metadata_status, {"error": "rejected"})
        return _Response(200, {})

    async def put(self, url, headers=None, json=None):
        self._record("PUT", url, json)
        if "/aspects" in url:
            return _Response(type(self).aspect_status, {})
        if "/collections/" in url:
            return _Response(type(self).collection_status, {})
        return _Response(200, {})

    async def get(self, url, headers=None):
        self._record("GET", url)
        return _Response(
            200,
            {
                "node": {
                    "properties": {},
                    "createdAt": type(self).created,
                    "aspects": [],
                }
            },
        )

    async def delete(self, url, headers=None):
        self._record("DELETE", url)
        return _Response(200, {})


@pytest.fixture
def repo(monkeypatch):
    _Client.calls = []
    _Client.metadata_status = 200
    _Client.extended_status = 200
    _Client.aspect_status = 200
    _Client.collection_status = 200
    _Client.created = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(repo_module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(repo_module.httpx, "Timeout", lambda *a, **k: None)
    return _Client


def _calls(method, fragment):
    return [c for c in _Client.calls if c["method"] == method and fragment in c["url"]]


async def _upload(**kwargs):
    kwargs.setdefault("check_duplicates", False)
    kwargs.setdefault("write_extended_data", False)
    return await RepositoryService("user", "password").upload_metadata(
        metadata=METADATA, context="default", version="2.0.0", **kwargs
    )


async def _save(**kwargs):
    return await RepositoryService("user", "password").save_node(
        metadata=METADATA,
        context="default",
        version="2.0.0",
        workflow_steps=[DEFAULT_WORKFLOW_STATUS],
        **kwargs,
    )


# ------------------------------------------- a rejected metadata write stops


@pytest.mark.asyncio
async def test_a_rejected_metadata_write_queues_nothing_and_discards_the_node(repo):
    repo.metadata_status = 400

    result = await _upload(start_workflow=True)

    assert result["success"] is False
    assert result["step"] == "setMetadata"
    assert result["discarded_node"] == NODE_ID
    assert not _calls("PUT", "/collections/")
    assert not _calls("PUT", "/workflow")
    assert _calls("DELETE", NODE_ID)


@pytest.mark.asyncio
async def test_save_node_stops_the_same_way(repo):
    repo.metadata_status = 400

    result = await _save()

    assert result["success"] is False
    assert result["step"] == "setMetadata"
    assert result["discarded_node"] == NODE_ID
    assert not _calls("PUT", "/collections/")
    assert not _calls("PUT", "/workflow")


@pytest.mark.asyncio
async def test_a_handed_over_node_is_not_discarded_but_left_out_of_the_queue(repo):
    repo.metadata_status = 400

    result = await _upload(start_workflow=True, node_id=NODE_ID)

    assert result["success"] is False
    assert "discarded_node" not in result
    assert not _calls("DELETE", NODE_ID)
    assert not _calls("PUT", "/workflow")


# --------------------------------------- an unloadable schema writes nothing


@pytest.mark.asyncio
async def test_without_any_repo_field_no_node_is_created(repo, monkeypatch):
    monkeypatch.setattr(repo_module, "get_repo_fields", lambda *a, **k: set())

    result = await _upload()

    assert result["success"] is False
    assert not _calls("POST", "/children?")


@pytest.mark.asyncio
async def test_save_node_refuses_without_any_repo_field_too(repo, monkeypatch):
    monkeypatch.setattr(repo_module, "get_repo_fields", lambda *a, **k: set())

    result = await _save()

    assert result["success"] is False
    assert not _calls("POST", "/children?")


# ------------------------------------------------- partial failures are told


@pytest.mark.asyncio
async def test_a_failed_collection_reference_is_named_in_the_error(repo):
    repo.collection_status = 403

    result = await _upload(start_workflow=False)

    assert result["success"] is True
    assert "Sammlung" in result["error"]


@pytest.mark.asyncio
async def test_a_failed_extended_write_is_named_in_the_error(repo):
    repo.extended_status = 500

    result = await _upload(start_workflow=False, write_extended_data=True)

    assert result["success"] is True
    assert "Erweiterte Daten" in result["error"]


@pytest.mark.asyncio
async def test_a_failed_aspect_is_named_in_the_error(repo):
    repo.aspect_status = 500
    metadata = {**METADATA, "cm:author": ["Erika Mustermann"]}

    result = await RepositoryService("user", "password").upload_metadata(
        metadata=metadata,
        context="default",
        version="2.0.0",
        check_duplicates=False,
        write_extended_data=False,
        start_workflow=False,
    )

    assert "Aspekt" in result["error"]


@pytest.mark.asyncio
async def test_a_failed_workflow_step_is_named_in_the_error(repo, monkeypatch):
    async def failing_put(self, url, headers=None, json=None):
        self._record("PUT", url, json)
        return _Response(403 if "/workflow" in url else 200, {})

    monkeypatch.setattr(_Client, "put", failing_put)

    result = await _upload(start_workflow=True)

    assert "Workflow" in result["error"]


# -------------------------------------------------- the edit window holds


@pytest.mark.asyncio
async def test_upload_into_an_old_node_is_refused_like_save_node(repo):
    repo.created = "2020-01-01T00:00:00Z"

    result = await _upload(node_id=OLD_NODE_ID)

    assert result["success"] is False
    assert result["error_code"] == "node_too_old"
    assert not _calls("POST", "METADATA_UPDATE")


# ------------------------------------------------ the workflow stops early


class _WorkflowClient:
    class _Response:
        def __init__(self, status_code):
            self.status_code = status_code
            self.text = "forbidden"

    def __init__(self, failing_status):
        self.failing_status = failing_status
        self.puts = []

    async def put(self, url, headers=None, json=None):
        self.puts.append(json["status"])
        return self._Response(403 if json["status"] == self.failing_status else 200)


@pytest.mark.asyncio
async def test_a_failed_approval_does_not_publish():
    client = _WorkflowClient("140_ELEMENT_LEGALLY_APPROVED")

    result = await run_workflow_steps(
        client,
        "Basic test",
        "https://repo.example/edu-sharing",
        NODE_ID,
        [
            DEFAULT_WORKFLOW_STATUS,
            "140_ELEMENT_LEGALLY_APPROVED",
            "150_PUBLISH_IN_SEARCH",
        ],
    )

    assert client.puts == [DEFAULT_WORKFLOW_STATUS, "140_ELEMENT_LEGALLY_APPROVED"]
    assert result["success"] is False
    assert [s["status"] for s in result["steps"]] == [
        DEFAULT_WORKFLOW_STATUS,
        "140_ELEMENT_LEGALLY_APPROVED",
        "150_PUBLISH_IN_SEARCH",
    ]
    assert result["steps"][2]["skipped"] is True
    assert result["steps"][2]["success"] is False


# ------------------------------------------- collection ids are node ids


@pytest.mark.parametrize("value", ["x%23", "x%3Fforce=true", "../../admin", "abc"])
def test_a_collection_id_that_is_no_node_id_is_refused_at_the_boundary(value):
    with pytest.raises(ValidationError):
        UploadRequest(metadata={}, collection_id=[value])


@pytest.mark.asyncio
async def test_a_collection_id_from_the_metadata_never_reaches_the_url():
    class _Recorder:
        def __init__(self):
            self.urls = []

        async def put(self, url, headers=None):
            self.urls.append(url)
            return _Response(200, {})

    client = _Recorder()

    result = await set_collections(
        client, "Basic x", "https://repo.example", NODE_ID, ["x#", COLLECTION_ID]
    )

    assert len(client.urls) == 1
    assert COLLECTION_ID in client.urls[0]
    assert result["results"][0]["success"] is False


# ----------------------------------------------- the duplicate check is told


@pytest.mark.asyncio
async def test_a_failed_duplicate_check_is_named_instead_of_passing_silently(
    repo, monkeypatch
):
    original_post = _Client.post

    async def search_fails(self, url, headers=None, json=None, files=None):
        if "/search/" in url:
            self._record("POST", url, json)
            return _Response(500, {})
        return await original_post(self, url, headers=headers, json=json, files=files)

    monkeypatch.setattr(_Client, "post", search_fails)

    result = await _upload(check_duplicates=True, start_workflow=False)

    assert result["success"] is True
    assert "Dublettenprüfung" in result["error"]


@pytest.mark.asyncio
async def test_the_duplicate_check_searches_one_url_even_from_a_list(repo):
    metadata = {**METADATA, "ccm:wwwurl": ["https://example.org/a"]}

    await RepositoryService("user", "password").upload_metadata(
        metadata=metadata,
        context="default",
        version="2.0.0",
        check_duplicates=True,
        write_extended_data=False,
        start_workflow=False,
    )

    (search,) = _calls("POST", "/search/")
    assert search["json"]["criteria"][0]["values"] == ["https://example.org/a"]


# ------------------------------------------- /node without a repository


def test_node_without_repository_credentials_answers_503(monkeypatch):
    from fastapi.testclient import TestClient

    from src import main

    monkeypatch.setattr(deps, "get_repository_service", lambda: None)

    response = TestClient(main.app).post(
        "/node", json={"metadata": {"cclom:title": "Titel"}}
    )

    assert response.status_code == 503


@pytest.mark.parametrize(
    "error_code, status", [("node_too_old", 403), ("node_not_found", 404)]
)
def test_upload_answers_a_refused_node_like_nodes_does(monkeypatch, error_code, status):
    from fastapi.testclient import TestClient

    from src import main

    class _Refusing:
        async def write_node(self, job, deadline=None):
            return {"success": False, "error_code": error_code, "error": "abgelehnt"}

    monkeypatch.setattr(deps, "get_repository_service", lambda: _Refusing())

    response = TestClient(main.app).post(
        "/upload", json={"cclom:title": "Titel", "node_id": NODE_ID}
    )

    assert response.status_code == status


def test_a_skipped_workflow_step_is_marked_in_the_api_answer(monkeypatch):
    from fastapi.testclient import TestClient

    from src import main

    class _Stopped:
        async def write_node(self, job, deadline=None):
            return {
                "success": True,
                "node": {"nodeId": NODE_ID},
                "workflow": [
                    {
                        "status": "140_ELEMENT_LEGALLY_APPROVED",
                        "success": False,
                        "error": "HTTP 403",
                    },
                    {
                        "status": "150_PUBLISH_IN_SEARCH",
                        "success": False,
                        "skipped": True,
                        "error": "Nicht ausgeführt",
                    },
                ],
            }

    monkeypatch.setattr(deps, "get_repository_service", lambda: _Stopped())

    response = TestClient(main.app).post("/upload", json={"cclom:title": "Titel"})

    steps = response.json()["workflow"]
    assert steps[1]["skipped"] is True
    assert not steps[0].get("skipped")
