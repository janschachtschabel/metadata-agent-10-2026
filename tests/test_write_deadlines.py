"""
Writes to the repository end inside their time budget and leave no orphan
(audit 2026-10-18: D-01, D-02, D-04, D-05, P-01).

- /upload and /nodes had no overall limit. A rollout (grace period 120 s) or
  the ingress cut them off after the node existed but before its metadata or
  workflow - an inbox node with a title and nothing else, and a bare 504.
- When the answer to "create node" got lost, the node existed and nobody knew
  its id: _failed had nothing to discard.
- The field-by-field fallback after a refused bulk write had no total budget.
- /upload did not report a failed preview, /nodes did.
- /detect and /extract-field had no overall limit; /generate waited for the
  screenshot outside its deadline.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services import repository_metadata as metadata_module
from src.services import repository_nodes as nodes_module
from src.services import repository_service as repo_module
from src.services.repository_service import RepositoryService

NODE_ID = "5ab4b434-4832-45ca-b4b4-34483265ca5d"
URL = "https://example.org/bruchrechnung"
METADATA = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
    "cclom:title": "Bruchrechnung Klasse 6",
    "cclom:general_description": "Arbeitsblatt zur Bruchrechnung.",
    "ccm:wwwurl": URL,
}


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


class _Repository:
    """Stands in for httpx.AsyncClient; subclasses change single answers."""

    calls: list[tuple[str, str]] = []
    inbox: list[dict] = []

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
        return _Response(200, {})

    async def get(self, url, headers=None):
        type(self).calls.append(("GET", url))
        if "/children?" in url:
            return _Response(200, {"nodes": type(self).inbox})
        created = datetime.now(timezone.utc).isoformat()
        return _Response(200, {"node": {"properties": {}, "createdAt": created}})

    async def delete(self, url, headers=None):
        type(self).calls.append(("DELETE", url))
        return _Response(200, {})


def _use(monkeypatch, client_class, inbox=()):
    client_class.calls = []
    client_class.inbox = list(inbox)
    monkeypatch.setattr(repo_module.httpx, "AsyncClient", client_class)
    monkeypatch.setattr(repo_module.httpx, "Timeout", lambda *a, **k: None)
    return client_class.calls


def _deletes(calls):
    return [url for method, url in calls if method == "DELETE"]


async def _upload(**options):
    return await RepositoryService("user", "password").upload_metadata(
        metadata=dict(METADATA),
        check_duplicates=False,
        context="default",
        version="2.0.0",
        write_extended_data=False,
        **options,
    )


# ------------------------------------------------- D-01: lost create answer


class _AnswerLost(_Repository):
    async def post(self, url, headers=None, json=None, files=None):
        if "/children?" in url:
            type(self).calls.append(("POST", url))
            raise httpx.ReadTimeout("answer lost")
        return await super().post(url, headers=headers, json=json, files=files)


def _inbox_node(node_id, created, url=URL):
    return {
        "ref": {"id": node_id},
        "createdAt": created.isoformat(),
        # A node nobody wrote to since its creation, like a lost one of ours.
        "modifiedAt": created.isoformat(),
        "properties": {"ccm:wwwurl": [url], "cclom:title": [METADATA["cclom:title"]]},
    }


@pytest.mark.asyncio
async def test_a_node_whose_create_answer_got_lost_is_found_and_discarded(monkeypatch):
    now = datetime.now(timezone.utc)
    calls = _use(monkeypatch, _AnswerLost, inbox=[_inbox_node(NODE_ID, now)])

    result = await _upload()

    assert result["success"] is False
    assert _deletes(calls) and NODE_ID in _deletes(calls)[0]
    assert result["discarded_node"] == NODE_ID


@pytest.mark.asyncio
async def test_an_older_node_with_the_same_url_is_left_alone(monkeypatch):
    older = datetime.now(timezone.utc) - timedelta(days=3)
    calls = _use(monkeypatch, _AnswerLost, inbox=[_inbox_node("other-node", older)])

    result = await _upload()

    assert result["success"] is False
    assert _deletes(calls) == []
    assert "Eingangsordner" in result["error"]


# ------------------------------------------- D-02: budget of the fallback


class _BulkRefused(_Repository):
    async def post(self, url, headers=None, json=None, files=None):
        if "METADATA_UPDATE" in url or "/metadata?" in url:
            type(self).calls.append(("POST", url))
            return _Response(400, {"error": "refused"})
        return await super().post(url, headers=headers, json=json, files=files)


@pytest.mark.asyncio
async def test_the_field_by_field_fallback_stops_at_its_budget(monkeypatch):
    calls = _use(monkeypatch, _BulkRefused)
    monkeypatch.setattr(metadata_module, "FIELD_FALLBACK_SECONDS", 0)

    result = await _upload()

    metadata_posts = [u for m, u in calls if m == "POST" and "/metadata" in u]
    assert len(metadata_posts) == 1, "only the bulk write; no single-field write"
    assert result["field_errors"]
    assert all("Zeitbudget" in e["error"] for e in result["field_errors"])


# ------------------------------------------ D-04: cancelled inside a write


class _Hanging(_Repository):
    """Creates the node, then the metadata write never answers."""

    async def post(self, url, headers=None, json=None, files=None):
        if "/children?" in url:
            return await super().post(url, headers=headers, json=json, files=files)
        type(self).calls.append(("POST", url))
        await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_a_write_cut_off_by_its_deadline_discards_the_node_it_created(
    monkeypatch,
):
    calls = _use(monkeypatch, _Hanging)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):
            await _upload()

    assert _deletes(calls) and NODE_ID in _deletes(calls)[0]


class _SlowCapture:
    async def capture(self, url, method=None):
        await asyncio.sleep(3600)


def test_upload_answers_504_at_its_deadline(monkeypatch):
    calls = _use(monkeypatch, _Hanging)
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )
    monkeypatch.setattr(deps, "get_screenshot_service", lambda: _SlowCapture())
    monkeypatch.setattr(deps.settings, "upload_timeout", 0.2)

    with TestClient(main.app) as client:
        response = client.post("/upload", json={**METADATA, "check_duplicates": False})

    assert response.status_code == 504, response.text
    assert response.json()["error"]["code"] == "UPLOAD_TIMEOUT"
    assert _deletes(calls), "the half-written node is discarded"


def test_save_node_answers_504_at_its_deadline(monkeypatch):
    _use(monkeypatch, _Hanging)
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )
    monkeypatch.setattr(deps.settings, "upload_timeout", 0.2)

    with TestClient(main.app) as client:
        response = client.post("/nodes", json=METADATA)

    assert response.status_code == 504, response.text
    assert response.json()["error"]["code"] == "UPLOAD_TIMEOUT"


def test_the_default_upload_limit_leaves_room_for_the_cleanup():
    # 120 s ingress and grace period: deadline + lost-node lookup (5 s)
    # + discard (5 s) + preStop (5 s).
    assert deps.settings.upload_timeout + 15 <= 120


# ------------------------------------------------- D-05: preview problems


@pytest.mark.asyncio
async def test_upload_reports_a_failed_preview_like_save_node(monkeypatch):
    _use(monkeypatch, _Repository)

    async def no_image():
        raise RuntimeError("capture failed")

    result = await _upload(start_workflow=False, preview_source=no_image)

    assert result["success"] is True
    assert "Preview" in result["error"]


# --------------------------------------------- P-01: detect, extract, screenshot


class _HangingLLM:
    provider, model = "b-api-openai", "m"

    async def extract_field(self, **kwargs):
        await asyncio.sleep(3600)

    async def close(self):
        pass


class _HangingMetadataService:
    llm_service = _HangingLLM()

    async def detect_schema(self, *args, **kwargs):
        await asyncio.sleep(3600)


def test_extract_field_answers_504_at_its_deadline(monkeypatch):
    monkeypatch.setattr(deps, "get_llm_service", lambda **kw: _HangingLLM())
    monkeypatch.setattr(deps.settings, "generate_timeout", 0.1)

    with TestClient(main.app) as client:
        response = client.post(
            "/extract-field",
            json={
                "text": "Ein Kurs.",
                "field_id": "cclom:title",
                "schema_file": "core.json",
            },
        )

    assert response.status_code == 504, response.text
    assert response.json()["error"]["code"] == "GENERATION_TIMEOUT"


def test_detect_content_type_answers_504_at_its_deadline(monkeypatch):
    monkeypatch.setattr(
        deps, "get_metadata_service", lambda **kw: _HangingMetadataService()
    )
    monkeypatch.setattr(deps.settings, "generate_timeout", 0.1)

    with TestClient(main.app) as client:
        response = client.post("/detect-content-type", json={"text": "Ein Kurs."})

    assert response.status_code == 504, response.text
    assert response.json()["error"]["code"] == "GENERATION_TIMEOUT"


def test_an_unknown_field_is_refused_before_the_source_is_fetched(monkeypatch):
    class _NoFetch:
        async def fetch_from_url(self, *args, **kwargs):
            raise AssertionError("fetched although the field does not exist")

    monkeypatch.setattr(deps, "get_input_source_service", lambda: _NoFetch())

    with TestClient(main.app) as client:
        response = client.post(
            "/extract-field",
            json={
                "input_source": "url",
                "source_url": "https://example.org/kurs",
                "field_id": "gibt:es_nicht",
                "schema_file": "core.json",
            },
        )

    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_generate_waits_for_the_screenshot_only_until_its_deadline():
    from src.api.routers.generate import _screenshot_data_url

    capture = asyncio.create_task(asyncio.sleep(3600))
    deadline = asyncio.get_running_loop().time() + 0.05

    assert await _screenshot_data_url(capture, deadline) is None
    await asyncio.sleep(0)
    assert capture.cancelled()


# --------------------------------------------- API-05: bounded correction


def test_a_correction_instruction_has_its_own_length_limit():
    from fastapi import HTTPException

    from src.api.input_resolver import MAX_CORRECTION_LENGTH, with_correction

    with pytest.raises(HTTPException) as refused:
        with_correction("Quelle", {}, "x" * (MAX_CORRECTION_LENGTH + 1))

    assert refused.value.status_code == 400
    text, _ = with_correction("Quelle", {}, "Titel kürzer")
    assert text.endswith("Titel kürzer")


@pytest.mark.asyncio
async def test_two_candidates_for_a_lost_node_discard_neither(monkeypatch):
    # Review finding 5: a concurrent upload or retry of the same URL may have
    # created the second one; discarding it would bin someone else's node.
    now = datetime.now(timezone.utc)
    calls = _use(
        monkeypatch,
        _AnswerLost,
        inbox=[_inbox_node("node-a", now), _inbox_node("node-b", now)],
    )

    result = await _upload()

    assert _deletes(calls) == []
    assert "Eingangsordner" in result["error"]


# ------------------------- review: the deadline's outcome is reported truthfully


def _deadline(seconds):
    return asyncio.get_running_loop().time() + seconds


@pytest.mark.asyncio
async def test_a_write_at_its_deadline_reports_the_discarded_node(monkeypatch):
    calls = _use(monkeypatch, _Hanging)

    result = await _upload(deadline=_deadline(0.1))

    assert result["error_code"] == "upload_timeout"
    assert result["discarded_node"] == NODE_ID
    assert _deletes(calls)


class _DiscardFails(_Hanging):
    async def delete(self, url, headers=None):
        type(self).calls.append(("DELETE", url))
        return _Response(503, {})


@pytest.mark.asyncio
async def test_a_failed_discard_at_the_deadline_hands_the_node_back(monkeypatch):
    _use(monkeypatch, _DiscardFails)

    result = await _upload(deadline=_deadline(0.1))

    assert "discarded_node" not in result
    assert result["node"]["nodeId"] == NODE_ID
    assert NODE_ID in result["error"]


class _WorkflowHangs(_Repository):
    """Only the workflow PUT hangs; everything before it answers at once."""

    async def put(self, url, headers=None, json=None):
        if not url.endswith("/workflow"):
            return await super().put(url, headers=headers, json=json)
        type(self).calls.append(("PUT", url))
        await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_a_node_already_in_the_workflow_is_reported_not_discarded(monkeypatch):
    calls = _use(monkeypatch, _WorkflowHangs)

    # Long enough that the steps before the workflow never meet the deadline,
    # even on a slow runner: it has to hit the hanging PUT.
    result = await _upload(deadline=_deadline(1.0))

    assert _deletes(calls) == [], "editors may already have been notified"
    assert result["node"]["nodeId"] == NODE_ID
    assert result["error_code"] == "upload_timeout"


def test_the_504_carries_what_happened_to_the_node(monkeypatch):
    _use(monkeypatch, _Hanging)
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )
    monkeypatch.setattr(deps.settings, "upload_timeout", 0.2)

    with TestClient(main.app) as client:
        response = client.post("/nodes", json=METADATA)

    assert response.status_code == 504
    assert response.json()["discarded_node"] == NODE_ID


def test_generate_refuses_a_bad_request_before_fetching_the_source(monkeypatch):
    # Review finding 9: /generate fetched the page before cheap refusals.
    class _NoFetch:
        async def fetch_from_url(self, *args, **kwargs):
            raise AssertionError("fetched although the request is refused")

    monkeypatch.setattr(deps, "get_input_source_service", lambda: _NoFetch())

    with TestClient(main.app) as client:
        unknown_schema = client.post(
            "/generate",
            json={
                "input_source": "url",
                "source_url": "https://example.org/kurs",
                "schema_file": "http://w3id.org/gibt/es/nicht",
            },
        )
        # A text over the correction limit is no longer refused: since the final
        # audit of 18.10. it is a new source (tests/test_generate_final_audit.py).

    assert unknown_schema.status_code == 400, unknown_schema.text


# ------------- release review 2026-10-18 (M2): a finished write is never undone


async def _never():
    await asyncio.sleep(3600)


def _workflow_puts(calls):
    return [url for method, url in calls if method == "PUT" and "/workflow" in url]


async def _wait_for(condition):
    async with asyncio.timeout(2):
        while not condition():
            await asyncio.sleep(0.01)


async def _within(coroutine, seconds=2.0):
    """The result, or a failure instead of a test that hangs (also in a shield)."""
    task = asyncio.ensure_future(coroutine)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if not done:
        task.cancel()
        pytest.fail(f"still running after {seconds} s")
    return task.result()


@pytest.mark.asyncio
async def test_a_slow_capture_is_given_up_and_the_node_kept(monkeypatch):
    calls = _use(monkeypatch, _Repository)
    monkeypatch.setattr(repo_module, "PREVIEW_RESERVE_SECONDS", 0.8)

    result = await _upload(preview_source=_never, deadline=_deadline(1.0))

    assert result["success"] is True, result
    assert result["preview"]["success"] is False
    assert _workflow_puts(calls), "the workflow still runs"
    assert _deletes(calls) == []


@pytest.mark.asyncio
async def test_without_time_left_the_preview_is_not_awaited(monkeypatch):
    calls = _use(monkeypatch, _Repository)
    awaited = []

    async def capture():
        awaited.append(True)
        await asyncio.sleep(3600)

    # Less time left than the reserve the workflow needs.
    result = await _upload(preview_source=capture, deadline=_deadline(1.0))

    assert result["success"] is True, result
    assert awaited == []
    assert result["preview"]["success"] is False
    assert _workflow_puts(calls)


class _ReadBackHangs(_Repository):
    """Every read of a node's metadata hangs (read-back and edit-window check)."""

    async def get(self, url, headers=None):
        if "/metadata?" in url:
            type(self).calls.append(("GET", url))
            await asyncio.sleep(3600)
        return await super().get(url, headers=headers)


@pytest.mark.asyncio
async def test_a_slow_read_back_does_not_undo_the_write(monkeypatch):
    calls = _use(monkeypatch, _ReadBackHangs)
    monkeypatch.setattr(repo_module, "READ_BACK_SECONDS", 0.1)

    result = await _upload(
        start_workflow=False, return_full_node=True, deadline=_deadline(0.3)
    )

    assert result["success"] is True, result
    assert result["node_full"] is None
    assert _deletes(calls) == []


@pytest.mark.asyncio
async def test_a_client_leaving_during_the_read_back_keeps_the_node(monkeypatch):
    calls = _use(monkeypatch, _ReadBackHangs)

    task = asyncio.create_task(_upload(start_workflow=False, return_full_node=True))
    await _wait_for(lambda: any(method == "GET" for method, _ in calls))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _deletes(calls) == []


# ---------------- release review 2026-10-18 (M3): failures after the workflow


@pytest.mark.asyncio
async def test_a_number_as_description_does_not_discard_a_node_in_the_workflow(
    monkeypatch,
):
    calls = _use(monkeypatch, _Repository)

    result = await RepositoryService("u", "p").upload_metadata(
        metadata={**METADATA, "cclom:general_description": 123},
        check_duplicates=False,
        context="default",
        version="2.0.0",
        write_extended_data=False,
    )

    assert _deletes(calls) == []
    assert result["success"] is True, result
    assert result["node"]["description"] == "123"


@pytest.mark.asyncio
async def test_an_unexpected_error_after_the_workflow_keeps_the_node(monkeypatch):
    calls = _use(monkeypatch, _Repository)

    def broken(*args, **kwargs):
        raise RuntimeError("bug after the workflow")

    monkeypatch.setattr(RepositoryService, "_written_result", broken)

    result = await _upload()

    assert _workflow_puts(calls)
    assert _deletes(calls) == [], "editors may already have been notified"
    assert result["success"] is False
    assert result["node"]["nodeId"] == NODE_ID


class _NoCapture:
    async def capture(self, url, method=None):
        raise RuntimeError("no browser in tests")


def test_a_number_as_title_is_answered_not_a_500(monkeypatch):
    _use(monkeypatch, _Repository)
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )
    monkeypatch.setattr(deps, "get_screenshot_service", lambda: _NoCapture())

    with TestClient(main.app) as client:
        response = client.post(
            "/upload",
            json={
                **METADATA,
                "cclom:title": 123,
                "check_duplicates": False,
                "write_extended_data": False,
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["node"]["title"] == "123"


# ------------ release review 2026-10-18 (#6): whose node a lost answer made


@pytest.mark.asyncio
async def test_a_lost_node_candidate_already_in_the_workflow_is_left_alone(
    monkeypatch,
):
    # A concurrent upload of the same URL finished and handed its node over.
    node = _inbox_node("concurrent-upload", datetime.now(timezone.utc))
    node["properties"]["ccm:wf_status"] = ["200_tocheck"]
    calls = _use(monkeypatch, _AnswerLost, inbox=[node])

    result = await _upload()

    assert _deletes(calls) == []
    assert "Eingangsordner" in result["error"]


@pytest.mark.asyncio
async def test_a_lost_node_candidate_from_before_the_request_is_left_alone(
    monkeypatch,
):
    earlier = datetime.now(timezone.utc) - timedelta(seconds=20)
    calls = _use(monkeypatch, _AnswerLost, inbox=[_inbox_node("earlier", earlier)])

    result = await _upload()

    assert _deletes(calls) == []
    assert "Eingangsordner" in result["error"]


# ------------ release review 2026-10-18 (#7): cleanup has a total budget


class _DiscardHangs(_Hanging):
    async def delete(self, url, headers=None):
        type(self).calls.append(("DELETE", url))
        await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_a_hanging_discard_ends_within_its_budget(monkeypatch):
    _use(monkeypatch, _DiscardHangs)
    monkeypatch.setattr(nodes_module, "DISCARD_TIMEOUT_SECONDS", 0.1)

    result = await _within(_upload(deadline=_deadline(0.1)))

    assert "discarded_node" not in result
    assert result["node"]["nodeId"] == NODE_ID


class _LookupHangs(_AnswerLost):
    async def get(self, url, headers=None):
        if "/children?" in url:
            type(self).calls.append(("GET", url))
            await asyncio.sleep(3600)
        return await super().get(url, headers=headers)


@pytest.mark.asyncio
async def test_a_hanging_lookup_ends_within_its_budget(monkeypatch):
    calls = _use(monkeypatch, _LookupHangs)
    monkeypatch.setattr(nodes_module, "DISCARD_TIMEOUT_SECONDS", 0.1)

    result = await _within(_upload())

    assert _deletes(calls) == []
    assert "Eingangsordner" in result["error"]


# ------------- release review 2026-10-18 (#21): cut-offs not covered before


class _CreateHangs(_Repository):
    async def post(self, url, headers=None, json=None, files=None):
        if "/children?" in url:
            type(self).calls.append(("POST", url))
            await asyncio.sleep(3600)
        return await super().post(url, headers=headers, json=json, files=files)


@pytest.mark.asyncio
async def test_a_deadline_while_the_create_request_is_out_says_a_node_may_exist(
    monkeypatch,
):
    calls = _use(monkeypatch, _CreateHangs)

    result = await _upload(deadline=_deadline(0.1))

    assert result["error_code"] == "upload_timeout"
    assert "möglicherweise" in result["error"]
    assert _deletes(calls) == []


@pytest.mark.asyncio
async def test_a_client_leaving_while_the_create_is_out_discards_the_node_found(
    monkeypatch,
):
    now = datetime.now(timezone.utc)
    calls = _use(monkeypatch, _CreateHangs, inbox=[_inbox_node(NODE_ID, now)])

    task = asyncio.create_task(_upload())
    await _wait_for(lambda: bool(calls))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _deletes(calls) and NODE_ID in _deletes(calls)[0]


EXISTING_NODE = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"


@pytest.mark.asyncio
async def test_a_deadline_while_updating_a_handed_over_node_keeps_it(monkeypatch):
    calls = _use(monkeypatch, _Hanging)

    result = await _upload(node_id=EXISTING_NODE, deadline=_deadline(0.1))

    assert result["error_code"] == "upload_timeout"
    assert _deletes(calls) == []
    assert result["node"]["nodeId"] == EXISTING_NODE
    assert "teilweise" in result["error"]


@pytest.mark.asyncio
async def test_a_deadline_before_anything_was_written_says_the_node_is_unchanged(
    monkeypatch,
):
    # Cut off while the edit window is still being checked.
    calls = _use(monkeypatch, _ReadBackHangs)

    result = await _upload(node_id=EXISTING_NODE, deadline=_deadline(0.1))

    assert result["error_code"] == "upload_timeout"
    assert _deletes(calls) == []
    assert result["node"]["nodeId"] == EXISTING_NODE
    assert "nicht verändert" in result["error"]


# ------------------------- follow-up review 2026-10-18 of the fix round


@pytest.mark.asyncio
async def test_without_a_workflow_the_preview_gets_the_whole_budget(monkeypatch):
    # /nodes runs no workflow by default: nothing to hold time back for.
    _use(monkeypatch, _Repository)

    async def capture():
        await asyncio.sleep(0.05)
        return b"\x89PNG\r\n\x1a\n", "image/png"

    result = await _upload(
        start_workflow=False, preview_source=capture, deadline=_deadline(3.0)
    )

    assert result["preview"]["success"] is True, result


@pytest.mark.asyncio
async def test_a_handed_over_node_in_the_workflow_is_named_after_a_failure(
    monkeypatch,
):
    calls = _use(monkeypatch, _Repository)

    def broken(*args, **kwargs):
        raise RuntimeError("bug after the workflow")

    monkeypatch.setattr(RepositoryService, "_written_result", broken)

    result = await _upload(node_id=EXISTING_NODE)

    assert _deletes(calls) == []
    assert result["success"] is False
    assert result["node"]["nodeId"] == EXISTING_NODE


@pytest.mark.asyncio
async def test_a_lost_node_candidate_written_to_since_is_left_alone(monkeypatch):
    # A concurrent upload of the same URL is still filling its node in: it
    # has no workflow status yet, but it was modified after its creation.
    now = datetime.now(timezone.utc)
    node = _inbox_node("concurrent-upload", now)
    node["modifiedAt"] = (now + timedelta(seconds=20)).isoformat()
    calls = _use(monkeypatch, _AnswerLost, inbox=[node])

    result = await _upload()

    assert _deletes(calls) == []
    assert "Eingangsordner" in result["error"]


@pytest.mark.asyncio
async def test_a_lost_node_candidate_without_modification_time_is_left_alone(
    monkeypatch,
):
    node = _inbox_node("unknown", datetime.now(timezone.utc))
    del node["modifiedAt"]
    calls = _use(monkeypatch, _AnswerLost, inbox=[node])

    result = await _upload()

    assert _deletes(calls) == []
    assert "Eingangsordner" in result["error"]


class _SlowDiscard(_Hanging):
    """The metadata write hangs; the DELETE afterwards takes a moment."""

    done: list[str] = []

    async def delete(self, url, headers=None):
        type(self).calls.append(("DELETE", url))
        await asyncio.sleep(0.3)
        type(self).done.append(url)
        return _Response(200, {})


class _FailsThenSlowDiscard(_SlowDiscard):
    async def post(self, url, headers=None, json=None, files=None):
        if "/children?" in url:
            return await _Repository.post(self, url, headers=headers, json=json)
        raise RuntimeError("metadata write broke")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client_class, options",
    [
        (_SlowDiscard, {"deadline_after": 0.1}),  # cleanup after the deadline
        (_FailsThenSlowDiscard, {}),  # discard after a failed write
    ],
)
async def test_a_cancel_during_the_cleanup_does_not_stop_the_delete(
    monkeypatch, client_class, options
):
    calls = _use(monkeypatch, client_class)
    client_class.done = []
    deadline = (
        _deadline(options["deadline_after"]) if "deadline_after" in options else None
    )

    task = asyncio.create_task(_upload(deadline=deadline))
    await _wait_for(lambda: bool(_deletes(calls)))
    task.cancel()  # the client goes away while the DELETE is out
    with pytest.raises(asyncio.CancelledError):
        await task
    await _wait_for(lambda: bool(client_class.done))

    assert NODE_ID in client_class.done[0]
