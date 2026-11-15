"""
A write cut off while it references the node in collections keeps the node
(audit 14.11., R-01, R-02).

- Once the first collection request is out, the node counts as kept, like a
  node in the workflow: a reference may exist, and discarding the node would
  leave it pointing at a node in the bin. The answer names the node and says
  which collections were linked and which not.
- The preview holds time back for the collection requests that follow it, not
  only for the workflow. Without that a slow capture used the deadline up and
  the finished node was discarded during the collection step.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from src.services import repository_preview_upload as preview_module
from src.services import repository_service as repo_module
from src.services.repository_service import NodeWrite, RepositoryService

NODE_ID = "5ab4b434-4832-45ca-b4b4-34483265ca5d"
COLLECTION_A = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"
COLLECTION_B = "4039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"
COLLECTION_C = "5039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"
METADATA = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
    "cclom:title": "Bruchrechnung Klasse 6",
    "ccm:wwwurl": "https://example.org/bruchrechnung",
}


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


class _Repository:
    """Stands in for httpx.AsyncClient; the PUTs for `slow` collections take `seconds`."""

    calls: list[tuple[str, str]] = []
    slow: dict[str, float] = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None, files=None):
        type(self).calls.append(("POST", url))
        if "/children?" in url:
            return _Response(200, {"node": {"ref": {"id": NODE_ID}}})
        return _Response(200, {})

    async def put(self, url, headers=None, json=None):
        type(self).calls.append(("PUT", url))
        for collection, seconds in type(self).slow.items():
            if collection in url:
                # The repository has the request; only its answer is slow.
                await asyncio.sleep(seconds)
        return _Response(200, {})

    async def get(self, url, headers=None):
        type(self).calls.append(("GET", url))
        created = datetime.now(timezone.utc).isoformat()
        return _Response(200, {"node": {"properties": {}, "createdAt": created}})

    async def delete(self, url, headers=None):
        type(self).calls.append(("DELETE", url))
        return _Response(200, {})


@pytest.fixture
def repository(monkeypatch):
    _Repository.calls = []
    _Repository.slow = {}
    monkeypatch.setattr(repo_module.httpx, "AsyncClient", _Repository)
    monkeypatch.setattr(repo_module.httpx, "Timeout", lambda *a, **k: None)
    return _Repository


def _deletes(calls):
    return [url for method, url in calls if method == "DELETE"]


async def _write(deadline_after, **options):
    deadline = asyncio.get_running_loop().time() + deadline_after
    job = NodeWrite(
        metadata=dict(METADATA),
        context="default",
        version="2.0.0",
        write_extended_data=False,
        **options,
    )
    return await RepositoryService("user", "password").write_node(job, deadline)


# ------------------------------------------------------------------- R-01


async def test_a_deadline_during_the_collections_keeps_the_node(repository):
    repository.slow = {COLLECTION_B: 3600}

    result = await _write(
        1.0,
        collection_ids=[COLLECTION_A, COLLECTION_B, COLLECTION_C],
        workflow_steps=["200_tocheck"],
    )

    assert _deletes(repository.calls) == [], "a reference to the node may exist"
    assert result["error_code"] == "upload_timeout"
    assert "discarded_node" not in result
    assert result["node"]["nodeId"] == NODE_ID
    assert "nicht verworfen" in result["error"]


async def test_the_answer_says_which_collections_were_linked(repository):
    repository.slow = {COLLECTION_B: 3600}

    result = await _write(
        1.0, collection_ids=[COLLECTION_A, COLLECTION_B, COLLECTION_C]
    )

    by_id = {entry["collectionId"]: entry for entry in result["collections"]}
    assert by_id[COLLECTION_A]["success"] is True
    # Sent, never answered: the reference may exist.
    assert by_id[COLLECTION_B]["success"] is False
    assert "nicht bestätigt" in by_id[COLLECTION_B]["error"]
    assert by_id[COLLECTION_C]["success"] is False
    assert "nicht versucht" in by_id[COLLECTION_C]["error"]
    # The 504 carries `error` and `node` only: the text has to name them too.
    assert COLLECTION_A in result["error"]
    assert COLLECTION_B in result["error"]
    assert COLLECTION_C in result["error"]


async def test_a_client_leaving_during_the_collections_keeps_the_node(repository):
    repository.slow = {COLLECTION_A: 3600}

    task = asyncio.create_task(_write(3600, collection_ids=[COLLECTION_A]))
    async with asyncio.timeout(2):
        while not any("/collections/" in url for _, url in repository.calls):
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _deletes(repository.calls) == []


async def test_a_deadline_before_the_collections_still_discards_the_node(
    repository, monkeypatch
):
    # Nothing points at the node yet: the half-written node is litter.
    async def hanging(self, *args, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(RepositoryService, "_set_metadata", hanging)

    result = await _write(0.2, collection_ids=[COLLECTION_A])

    assert result["discarded_node"] == NODE_ID
    assert not [url for _, url in repository.calls if "/collections/" in url]


# ------------------------------------------------------------------- R-02


async def _never():
    await asyncio.sleep(3600)


async def test_the_preview_leaves_time_for_the_collections_without_a_workflow(
    repository, monkeypatch
):
    # The auditor's case B: preview never ready, one collection whose answer
    # takes longer than the 1 s margin, no workflow.
    monkeypatch.setattr(preview_module, "PREVIEW_RESERVE_SECONDS", 2.5)
    repository.slow = {COLLECTION_A: 1.5}

    result = await _write(3.0, collection_ids=[COLLECTION_A], preview_source=_never)

    assert result["success"] is True, result
    assert result["preview"]["success"] is False
    assert result["collections"] == [{"collectionId": COLLECTION_A, "success": True}]
    assert _deletes(repository.calls) == []


@pytest.mark.parametrize(
    "collections, workflow, expected",
    [
        (0, 0, 1.0),  # nothing follows: only the margin
        (0, 1, 15.0),
        (1, 0, 15.0),
        (2, 1, 25.0),  # 15 s for the first request, 5 s for each further one
        (40, 3, 45.0),  # capped: the preview keeps most of the default 100 s
    ],
)
def test_the_reserve_grows_with_the_requests_that_follow(
    collections, workflow, expected
):
    assert repo_module.preview_reserve(collections, workflow) == expected
