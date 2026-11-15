"""
The duplicate check and the lookup of a lost node, against a fake repository
(audit 14.11., R-03, R-05, R-14, Q-03).

- Q-03: the path "duplicate found" of the real write service ran in no test:
  every fake answered the search with nothing.
- R-14: a hit without a title counted as "check not possible" and a second
  node was created.
- R-05: two uploads of one URL at the same time both passed the check (one
  search each, the index knows neither node yet) and created two nodes. Check
  and create now run one after the other per URL, and the check also reads the
  newest children of the inbox directly.
- R-03: when the answer to "create" got lost, a finished node of another save
  of the same URL could be taken for the lost one and discarded. A node that
  carries more than the create request sets is never ours.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from repository_fake import NODE_ID, FakeRepository, Response, install, requests
from src.services import repository_nodes as nodes_module
from src.services.repository_service import RepositoryService

URL = "https://example.org/bruchrechnung"
EXISTING = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"
OTHER_NODE = "99999999-4832-45ca-b4b4-34483265ca5d"
METADATA = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
    "cclom:title": "Bruchrechnung Klasse 6",
    "cclom:general_description": "Arbeitsblatt zur Bruchrechnung.",
    "ccm:wwwurl": URL,
}
HIT = {
    "ref": {"id": EXISTING},
    "title": "Schon vorhanden",
    "properties": {
        "cclom:title": ["Schon vorhanden"],
        "cclom:general_description": ["Liegt seit gestern im Repository."],
        "ccm:wwwurl": [URL],
    },
}


async def _upload(**options):
    options.setdefault("check_duplicates", True)
    return await RepositoryService("user", "password").upload_metadata(
        metadata=dict(METADATA),
        context="default",
        version="2.0.0",
        write_extended_data=False,
        **options,
    )


def _creates(calls):
    return requests(calls, "POST", "/children?")


# ------------------------------------------------------------------- Q-03


async def test_a_search_hit_refuses_the_upload_and_creates_nothing(monkeypatch):
    calls = install(monkeypatch, search_hits=[HIT])

    result = await _upload()

    assert result["success"] is False
    assert result["duplicate"] is True
    assert result["node"]["nodeId"] == EXISTING
    assert result["node"]["title"] == "Schon vorhanden"
    assert result["node"]["description"] == "Liegt seit gestern im Repository."
    assert result["node"]["wwwurl"] == URL
    assert "Schon vorhanden" in result["error"]
    assert "node_full" not in result
    assert _creates(calls) == []
    assert requests(calls, "PUT") == [], "no collection, no workflow"


async def test_with_return_full_node_the_duplicate_is_read_back(monkeypatch):
    calls = install(monkeypatch, search_hits=[HIT])

    result = await _upload(return_full_node=True)

    assert result["duplicate"] is True
    assert result["node_full"]["ref"]["id"] == NODE_ID  # what the fake's read returns
    assert len(requests(calls, "GET", f"/{EXISTING}/metadata")) == 1
    assert _creates(calls) == []


async def test_create_node_with_check_duplicates_refuses_a_known_url(monkeypatch):
    calls = install(monkeypatch, search_hits=[HIT])

    result = await RepositoryService("user", "password").create_node(
        dict(METADATA), check_duplicates=True
    )

    assert result["success"] is False
    assert result["duplicate"] is True
    assert result["node"]["nodeId"] == EXISTING
    assert _creates(calls) == []


class _SearchRaises(FakeRepository):
    async def answer(self, method, url, body):
        if "ngsearch" in url:
            raise httpx.ConnectError("search is down")
        return await super().answer(method, url, body)


async def test_a_search_that_raises_is_reported_and_the_node_still_created(
    monkeypatch,
):
    calls = install(monkeypatch, _SearchRaises)

    result = await _upload()

    assert result["success"] is True
    assert result["node"]["nodeId"] == NODE_ID
    assert len(_creates(calls)) == 1
    assert "Dublettenprüfung nicht möglich" in result["error"]
    assert "ConnectError" in result["error"]


# ------------------------------------------------------------------- R-14


@pytest.mark.parametrize(
    "hit",
    [
        {"ref": {"id": EXISTING}, "title": None, "properties": {"cclom:title": []}},
        {"ref": {"id": EXISTING}, "properties": {}},
        {"ref": {"id": EXISTING}},
    ],
)
async def test_a_hit_without_a_title_is_still_a_duplicate(monkeypatch, hit):
    calls = install(monkeypatch, search_hits=[hit])

    result = await _upload()

    assert result["duplicate"] is True
    assert result["node"]["nodeId"] == EXISTING
    assert result["node"]["title"] is None
    assert EXISTING in result["error"]
    assert "None" not in result["error"]
    assert _creates(calls) == []


# ------------------------------------------------------------------- R-05


class _LaggingIndex(FakeRepository):
    """The search knows no node created a moment ago; the inbox holds them."""

    async def answer(self, method, url, body):
        if "ngsearch" in url:
            await asyncio.sleep(0.05)
            return Response(200, {"nodes": []})
        if method == "POST" and "/children?" in url:
            number = len(type(self).inbox) + 1
            node_id = f"0000000{number}-4832-45ca-b4b4-34483265ca5d"
            now = datetime.now(timezone.utc).isoformat()
            type(self).inbox.insert(
                0,
                {
                    "ref": {"id": node_id},
                    "createdAt": now,
                    "modifiedAt": now,
                    "properties": dict(body),
                },
            )
            return Response(200, {"node": {"ref": {"id": node_id}}})
        return await super().answer(method, url, body)


async def test_two_uploads_of_one_url_at_once_create_one_node(monkeypatch):
    calls = install(monkeypatch, _LaggingIndex)

    results = await asyncio.gather(_upload(), _upload())

    assert len(_creates(calls)) == 1
    assert sorted(r["success"] for r in results) == [False, True]
    refused = next(r for r in results if not r["success"])
    written = next(r for r in results if r["success"])
    assert refused["duplicate"] is True
    assert refused["node"]["nodeId"] == written["node"]["nodeId"]


async def test_a_retry_finds_the_node_the_index_does_not_know_yet(monkeypatch):
    calls = install(monkeypatch, _LaggingIndex)

    first = await _upload()
    second = await _upload()

    assert first["success"] is True
    assert second["duplicate"] is True
    assert len(_creates(calls)) == 1


async def test_uploads_of_different_urls_do_not_wait_for_each_other(monkeypatch):
    install(monkeypatch, _LaggingIndex)

    async def upload(url):
        return await RepositoryService("user", "password").upload_metadata(
            metadata={**METADATA, "ccm:wwwurl": url},
            context="default",
            version="2.0.0",
            write_extended_data=False,
        )

    results = await asyncio.gather(upload(URL + "/a"), upload(URL + "/b"))

    assert [r["success"] for r in results] == [True, True]


async def test_the_lock_table_holds_no_url_after_the_uploads(monkeypatch):
    install(monkeypatch, _LaggingIndex)

    await asyncio.gather(_upload(), _upload(), _upload())

    assert nodes_module.url_locks.keys_in_use() == 0


class _CreateHangs(_LaggingIndex):
    async def answer(self, method, url, body):
        if method == "POST" and "/children?" in url:
            await asyncio.sleep(3600)
        return await super().answer(method, url, body)


async def test_a_cancelled_upload_releases_the_url(monkeypatch):
    calls = install(monkeypatch, _CreateHangs)

    holder = asyncio.create_task(_upload())
    waiter = asyncio.create_task(_upload())
    async with asyncio.timeout(2):
        while not _creates(calls):
            await asyncio.sleep(0.01)
    assert nodes_module.url_locks.keys_in_use() == 1
    for task in (waiter, holder):
        task.cancel()
    await asyncio.gather(holder, waiter, return_exceptions=True)

    assert nodes_module.url_locks.keys_in_use() == 0


async def test_an_unreadable_inbox_leaves_the_search_to_decide(monkeypatch):
    class _InboxRefused(FakeRepository):
        async def answer(self, method, url, body):
            if method == "GET" and "/children?" in url:
                return Response(403, {"message": "no"})
            return await super().answer(method, url, body)

    calls = install(monkeypatch, _InboxRefused)

    result = await _upload()

    assert result["success"] is True
    assert len(_creates(calls)) == 1


# ------------------------------------------------------------------- R-03


class _AnswerLost(FakeRepository):
    async def answer(self, method, url, body):
        if method == "POST" and "/children?" in url:
            raise httpx.ReadError("connection reset")
        return await super().answer(method, url, body)


def _inbox_node(node_id, **properties):
    now = datetime.now(timezone.utc)
    return {
        "ref": {"id": node_id},
        "createdAt": now.isoformat(),
        "modifiedAt": (now + timedelta(seconds=1)).isoformat(),
        "properties": {
            "ccm:wwwurl": [URL],
            "cclom:title": [METADATA["cclom:title"]],
            "cclom:general_description": [METADATA["cclom:general_description"]],
            "ccm:linktype": ["USER_GENERATED"],
            "cm:name": ["example.org_b"],
            "sys:node-uuid": [node_id],
            **properties,
        },
    }


@pytest.mark.parametrize(
    "written",
    [
        # The auditor's case 2: a finished save of the same URL.
        {
            "ccm:taxonid": ["http://w3id.org/openeduhub/vocabs/discipline/380"],
            "ccm:oeh_extendedData": ["{...}"],
        },
        {"ccm:taxonid": ["http://w3id.org/openeduhub/vocabs/discipline/380"]},
        {"ccm:oeh_extendedType": ["http://w3id.org/openeduhub/vocabs/contentTypes/x"]},
        {"ccm:commonlicense_key": ["CC_BY"]},
        {"ccm:lifecyclecontributer_author": ["BEGIN:VCARD\nFN:E M\nEND:VCARD"]},
        {"ccm:price": ["http://w3id.org/openeduhub/vocabs/price/no"]},
    ],
)
async def test_a_node_that_already_carries_metadata_is_not_taken_for_the_lost_one(
    monkeypatch, written
):
    calls = install(
        monkeypatch, _AnswerLost, inbox=[_inbox_node(OTHER_NODE, **written)]
    )

    result = await _upload(check_duplicates=False)

    assert requests(calls, "DELETE") == []
    assert result["success"] is False
    assert "discarded_node" not in result
    assert "node" not in result, "the other node's id is not acted on or handed out"
    assert "möglicherweise" in result["error"]


async def test_create_node_does_not_hand_out_another_saves_node(monkeypatch):
    other = _inbox_node(
        OTHER_NODE, **{"ccm:taxonid": ["http://w3id.org/openeduhub/vocabs/x/380"]}
    )
    install(monkeypatch, _AnswerLost, inbox=[other])

    result = await RepositoryService("user", "password").create_node(dict(METADATA))

    assert result["success"] is False
    assert "node" not in result
    assert "möglicherweise" in result["error"]


async def test_a_node_holding_only_what_create_sets_is_still_found(monkeypatch):
    calls = install(monkeypatch, _AnswerLost, inbox=[_inbox_node(NODE_ID)])

    result = await _upload(check_duplicates=False)

    assert result["discarded_node"] == NODE_ID
    assert len(requests(calls, "DELETE", NODE_ID)) == 1
