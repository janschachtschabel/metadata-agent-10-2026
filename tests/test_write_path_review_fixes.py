"""
Write-path findings of the release review 2.1.1 -> 3.0.0 (2026-10-18) that
have no better home in the deadline, preview or two-step tests.

- #9: a repository outage while reading a node was answered 404 "existiert
  nicht oder kein Zugriff" - the caller was told the node is gone.
- Pre-existing: the coarse LRT fallback overwrote a precise LRT already on the
  node; a partly written set of extended fields counted as success; a schema
  read that failed once left the vocabulary fields incomplete for good; raw
  exception texts reached clients; a failed capture task logged "Task
  exception was never retrieved".
"""

import asyncio
import gc
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services import repository_service as repo_module
from src.services.repository_service import RepositoryService

NODE_ID = "5ab4b434-4832-45ca-b4b4-34483265ca5d"
BASE_URL = "https://repo.example/edu-sharing"


class _Repository:
    """A fake edu-sharing behind httpx.MockTransport; answers by URL."""

    def __init__(self):
        self.node_status = 200
        self.properties: dict = {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if request.method == "GET" and "/metadata" in url:
            if self.node_status != 200:
                return httpx.Response(self.node_status, json={"message": "nope"})
            created = datetime.now(timezone.utc).isoformat()
            node = {"properties": self.properties, "aspects": [], "createdAt": created}
            return httpx.Response(200, json={"node": node})
        if "/children?" in url:
            return httpx.Response(200, json={"node": {"ref": {"id": NODE_ID}}})
        return httpx.Response(200, json={})


@pytest.fixture
def repository(monkeypatch):
    fake = _Repository()
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(fake.handler), **kwargs)

    monkeypatch.setattr(repo_module.httpx, "AsyncClient", client)
    return fake


async def _edit_check(repository):
    async with repo_module.httpx.AsyncClient() as client:
        return await RepositoryService("u", "p")._reject_if_too_old(
            client, BASE_URL, NODE_ID
        )


# ------------------------------------------------- #9: outage is not a 404


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_a_repository_outage_is_not_reported_as_a_missing_node(
    repository, status
):
    repository.node_status = status

    refusal = await _edit_check(repository)

    assert refusal["error_code"] == "node_unreadable"
    assert "existiert nicht" not in refusal["error"]


@pytest.mark.parametrize("status", [403, 404])
async def test_a_missing_or_forbidden_node_is_still_node_not_found(repository, status):
    repository.node_status = status

    refusal = await _edit_check(repository)

    assert refusal["error_code"] == "node_not_found"


async def test_an_unreachable_repository_is_not_reported_as_a_missing_node(
    monkeypatch,
):
    def unreachable(request):
        raise httpx.ConnectError("connection refused")

    real_client = httpx.AsyncClient
    async with real_client(transport=httpx.MockTransport(unreachable)) as client:
        refusal = await RepositoryService("u", "p")._reject_if_too_old(
            client, BASE_URL, NODE_ID
        )

    assert refusal["error_code"] == "node_unreadable"


def test_save_node_answers_502_when_the_repository_cannot_be_read(
    repository, monkeypatch
):
    repository.node_status = 503
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/nodes",
            json={
                "metadataset": "learning_material.json",
                "node_id": NODE_ID,
                "cclom:title": "Bruchrechnung",
            },
        )

    assert response.status_code == 502, response.text


# --------------------- pre-existing (1): the LRT fallback and a node's own LRT

ARBEITSBLATT = (
    "http://w3id.org/openeduhub/vocabs/new_lrt/36e68792-6159-481d-a97b-2c00901f4f78"
)


def _extended_write(repository):
    for request in repository.requests:
        if "EXTENDED_DATA" in str(request.url):
            return json.loads(request.read())
    raise AssertionError("no extended write")


async def _partial_update(repository):
    return await RepositoryService("u", "p").save_node(
        metadata={
            "metadataset": "learning_material.json",
            "cclom:general_keyword": ["Bruchrechnung"],
        },
        node_id=NODE_ID,
        context="default",
        version="2.0.0",
        write_extended_data=True,
    )


async def test_a_partial_update_keeps_the_precise_lrt_on_the_node(repository):
    repository.properties = {"ccm:oeh_lrt": [ARBEITSBLATT]}

    result = await _partial_update(repository)

    assert result["success"] is True, result
    assert "ccm:oeh_lrt" not in _extended_write(repository)


async def test_a_handed_over_node_without_lrt_still_gets_the_derived_one(repository):
    result = await _partial_update(repository)

    assert result["success"] is True, result
    assert "ccm:oeh_lrt" in _extended_write(repository)


# --------------- pre-existing (2): extended fields written only in part


async def test_extended_fields_written_only_in_part_are_named(repository):
    def handler(request):
        if "EXTENDED_DATA" in str(request.url):
            repository.requests.append(request)
            fields = json.loads(request.read())
            if len(fields) > 1 or "ccm:oeh_extendedText" in fields:
                return httpx.Response(400, json={"message": "refused"})
            return httpx.Response(200, json={})
        return _Repository.handler(repository, request)

    repository.handler = handler

    result = await RepositoryService("u", "p").upload_metadata(
        metadata={
            "metadataset": "learning_material.json",
            "cclom:title": "Bruchrechnung",
        },
        check_duplicates=False,
        context="default",
        version="2.0.0",
        write_extended_data=True,
        extended_text="Rohtext der Seite",
    )

    assert result["success"] is True, result
    assert "ccm:oeh_extendedText" in result["error"]


# --------- pre-existing (3): a failed schema read is not cached for good


def test_a_failed_schema_read_does_not_stay_in_the_vocabulary_cache(monkeypatch):
    from src.services import repository_diff

    failing = [True]

    def schema_fields(context, version, name):
        if failing[0]:
            raise OSError("schema not readable right now")
        return [{"id": "ccm:test_field", "system": {"vocabulary": {"concepts": [1]}}}]

    monkeypatch.setattr(repository_diff, "get_schema_fields", schema_fields)
    # A key no other test uses, so the cache holds nothing for it yet.
    args = ("cache-test-context", "9.9.9", None)

    assert "ccm:test_field" not in repository_diff.vocabulary_fields(*args)
    failing[0] = False
    assert "ccm:test_field" in repository_diff.vocabulary_fields(*args)


# ------------------ pre-existing (4): no raw exception texts for clients


def test_an_invalid_upload_body_names_the_field_not_the_raw_error(monkeypatch):
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/upload",
            json={"cclom:title": "T", "check_duplicates": "secret-value-xyz"},
        )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "check_duplicates" in detail
    assert "secret-value-xyz" not in detail
    assert "errors.pydantic.dev" not in detail


async def test_a_failed_preview_post_does_not_reveal_its_details():
    def broken(request):
        raise RuntimeError("connect 10.0.0.5:6379 from /srv/app")

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as client:
        result = await RepositoryService("u", "p")._post_preview(
            client, BASE_URL, NODE_ID, b"\x89PNG\r\n\x1a\n", "image/png"
        )

    assert result["success"] is False
    assert "RuntimeError" in result["error"]
    assert "10.0.0.5" not in result["error"]


# ------------ pre-existing (5): a failed capture's exception is retrieved


async def test_cancelling_a_failed_capture_retrieves_its_exception():
    from src.api.routers.upload import _PreviewCapture

    loop = asyncio.get_running_loop()
    reported = []
    loop.set_exception_handler(lambda loop, context: reported.append(context))
    try:

        async def failing():
            raise RuntimeError("capture failed")

        task = asyncio.create_task(failing())
        await asyncio.wait({task})
        _PreviewCapture(task).cancel()
        del task
        gc.collect()
    finally:
        loop.set_exception_handler(None)

    assert not [c for c in reported if "never retrieved" in c.get("message", "")]


# ------------------------------- nit: the 504 body is declared in the spec


def test_the_error_response_declares_what_a_504_names():
    from src.models.schemas import ErrorResponse

    properties = ErrorResponse.model_json_schema()["properties"]

    assert "node" in properties
    assert "discarded_node" in properties


# ------------ follow-up review: no raw exception texts in the write path

SECRET = "connect 10.0.0.5:6379 from /srv/app"


@pytest.mark.parametrize(
    "error",
    [RuntimeError(SECRET), httpx.ReadTimeout(SECRET), httpx.ConnectError(SECRET)],
    ids=["unexpected", "timeout", "connect"],
)
async def test_a_failed_write_names_the_error_type_not_its_text(repository, error):
    def handler(request):
        if "METADATA_UPDATE" in str(request.url):
            raise error
        return _Repository.handler(repository, request)

    repository.handler = handler

    result = await RepositoryService("u", "p").upload_metadata(
        metadata={"metadataset": "learning_material.json", "cclom:title": "T"},
        check_duplicates=False,
        context="default",
        version="2.0.0",
        write_extended_data=False,
    )

    assert result["success"] is False
    assert type(error).__name__ in result["error"]
    assert "10.0.0.5" not in result["error"]


async def test_a_failed_preview_source_names_the_error_type_not_its_text(repository):
    async def capture():
        raise RuntimeError(SECRET)

    async with repo_module.httpx.AsyncClient() as client:
        result = await RepositoryService("u", "p")._preview_from_source(
            client, BASE_URL, NODE_ID, capture
        )

    assert result["success"] is False
    assert "RuntimeError" in result["error"]
    assert "10.0.0.5" not in result["error"]


@pytest.mark.parametrize(
    "path, body, field",
    [
        (
            "/nodes",
            {"cclom:title": "T", "write_extended_data": "secret-value-xyz"},
            "write_extended_data",
        ),
        # A type error: the model's own message for an unknown status names
        # the status on purpose, pydantic's raw text would add the input.
        (
            f"/workflow/{NODE_ID}",
            {"steps": ["200_tocheck"], "comment": {"x": "secret-value-xyz"}},
            "comment",
        ),
    ],
)
def test_an_invalid_body_names_the_field_not_the_raw_error(
    monkeypatch, path, body, field
):
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )

    with TestClient(main.app) as client:
        response = client.post(path, json=body)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert field in detail
    assert "secret-value-xyz" not in detail
    assert "errors.pydantic.dev" not in detail


# ---------- S-06, last round: per-item and verify/workflow error texts

COLLECTION_ID = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"


def _raising_handler(error):
    def handler(request):
        raise error

    return handler


def _raising(error):
    return httpx.MockTransport(_raising_handler(error))


def _no_secret(text):
    return "10.0.0.5" not in text and "/srv/app" not in text


ERRORS = [RuntimeError(SECRET), httpx.ReadTimeout(SECRET), httpx.ConnectError(SECRET)]
ERROR_IDS = ["unexpected", "timeout", "connect"]


async def _verify_or_workflow(call):
    service = RepositoryService("u", "p")
    if call == "verify":
        return await service.verify_node(NODE_ID)
    return await service.set_workflow(NODE_ID, ["150_PUBLISH_IN_SEARCH"])


@pytest.mark.parametrize("error", ERRORS, ids=ERROR_IDS)
@pytest.mark.parametrize("call", ["verify", "workflow"])
async def test_verify_and_workflow_name_the_error_type_not_its_text(
    monkeypatch, error, call
):
    # The client itself fails: the handlers of verify_node / set_workflow answer.
    class _Unreachable:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise error

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(repo_module.httpx, "AsyncClient", _Unreachable)

    result = await _verify_or_workflow(call)

    assert result["success"] is False
    assert type(error).__name__ in result["error"], result
    assert _no_secret(result["error"]), result


@pytest.mark.parametrize("error", ERRORS, ids=ERROR_IDS)
async def test_a_workflow_whose_requests_fail_reveals_no_details(repository, error):
    repository.handler = _raising_handler(error)

    result = await _verify_or_workflow("workflow")

    assert result["success"] is False
    assert type(error).__name__ in str(result)
    assert _no_secret(str(result)), result


async def test_a_failed_collection_reference_names_the_type_not_the_text():
    from src.services.repository_curation import set_collections

    async with httpx.AsyncClient(transport=_raising(RuntimeError(SECRET))) as client:
        result = await set_collections(client, "", BASE_URL, NODE_ID, [COLLECTION_ID])

    entry = result["results"][0]
    assert entry["collectionId"] == COLLECTION_ID
    assert "RuntimeError" in entry["error"]
    assert _no_secret(entry["error"])


async def test_a_failed_workflow_step_names_the_type_not_the_text():
    from src.services.repository_curation import run_workflow_steps

    async with httpx.AsyncClient(transport=_raising(RuntimeError(SECRET))) as client:
        result = await run_workflow_steps(
            client, "", BASE_URL, NODE_ID, ["200_tocheck"]
        )

    step = result["steps"][0]
    assert step["status"] == "200_tocheck"
    assert "RuntimeError" in step["error"]
    assert _no_secret(step["error"])


async def test_a_field_that_raised_names_the_type_not_the_text():
    def handler(request):
        if len(json.loads(request.read())) > 1:
            return httpx.Response(400, json={"message": "bulk refused"})
        raise RuntimeError(SECRET)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await RepositoryService("u", "p")._set_metadata(
            client,
            BASE_URL,
            NODE_ID,
            {"cclom:title": "T", "cclom:general_keyword": ["k"]},
            {"cclom:title", "cclom:general_keyword"},
        )

    errors = {e["field_id"]: e["error"] for e in result["field_errors"]}
    assert set(errors) == {"cclom:title", "cclom:general_keyword"}
    assert all("RuntimeError" in e and _no_secret(e) for e in errors.values())


async def test_failed_extended_fields_name_the_type_not_the_text():
    async with httpx.AsyncClient(transport=_raising(RuntimeError(SECRET))) as client:
        result = await RepositoryService("u", "p")._write_extended_fields(
            client,
            BASE_URL,
            NODE_ID,
            {"metadataset": "learning_material.json", "cclom:title": "T"},
            "default",
            "2.0.0",
        )

    assert result["success"] is False
    assert "RuntimeError" in result["error"]
    assert _no_secret(result["error"])


async def test_undecodable_preview_data_keeps_the_explanation_only():
    from src.services.repository_preview import PreviewError, resolve_preview

    async with httpx.AsyncClient() as client:
        with pytest.raises(PreviewError) as refused:
            await resolve_preview(client, "data:image/png;base64,abc*")

    assert str(refused.value) == (
        "Preview ist weder eine http(s)-URL noch gültiges Base64."
    )


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout(SECRET), httpx.ConnectError(SECRET)]
)
async def test_a_failed_preview_fetch_names_the_type_not_the_text(error):
    from src.services.repository_preview import PreviewError, resolve_preview

    async with httpx.AsyncClient(transport=_raising(error)) as client:
        with pytest.raises(PreviewError) as refused:
            await resolve_preview(client, "http://93.184.216.34/image.png")

    message = str(refused.value)
    assert message.startswith("Preview-Bild konnte nicht geladen werden")
    assert type(error).__name__ in message
    assert _no_secret(message)


# --------- review 18.10. nit: the capture is cancelled exactly when refused


@pytest.mark.parametrize(
    "error_code, status",
    [("write_failed", 502), ("node_not_found", 404), ("upload_timeout", 504)],
)
def test_a_refused_upload_cancels_its_running_capture(monkeypatch, error_code, status):
    from types import SimpleNamespace

    started: list[asyncio.Task] = []

    async def capture(**kwargs):
        started.append(asyncio.current_task())
        await asyncio.sleep(3600)

    async def upload_metadata(**kwargs):
        await asyncio.sleep(0)  # the capture task starts
        return {"success": False, "error_code": error_code, "error": "nein"}

    monkeypatch.setattr(
        deps, "get_screenshot_service", lambda: SimpleNamespace(capture=capture)
    )
    monkeypatch.setattr(
        deps,
        "get_repository_service",
        lambda: SimpleNamespace(upload_metadata=upload_metadata),
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/upload",
            json={
                "metadata": {"cclom:title": "Kurs"},
                "preview_url": "https://example.org/",
            },
        )
        # Before the client's loop shuts down and cancels whatever is left.
        cancelled = bool(started) and started[0].cancelling() > 0

    assert response.status_code == status, response.text
    assert cancelled
