"""
Write path findings of the final pre-release audit (2026-10-18, strand A).

- API-11: /upload/verify answered a node the repository hides (403) with 502.
- API-12: /workflow/{node_id} had no deadline, an unknown node answered 200.
- A handed-over node whose bulk write raised after reaching the repository was
  answered without the node and without the "teilweise geschrieben" note.
- _ensure_aspects PUT only the new aspects when the GET of the current ones
  failed - the PUT replaces the list.
- S-17: /upload logged the full preview URL with its query.
- API-10: /upload/verify answered a refused body with pydantic's raw errors.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services import repository_service as repo_module
from src.services.repository_service import RepositoryService

NODE_ID = "5ab4b434-4832-45ca-b4b4-34483265ca5d"
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
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def _young_node():
    return {
        "node": {
            "ref": {"id": NODE_ID},
            "properties": {},
            "aspects": [],
            "createdAt": datetime.now(timezone.utc).isoformat(),
        }
    }


class _Repository:
    """Stands in for httpx.AsyncClient; subclasses change single answers."""

    calls: list[tuple[str, str]] = []

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
        return _Response(200, _young_node())

    async def delete(self, url, headers=None):
        type(self).calls.append(("DELETE", url))
        return _Response(200, {})


def _use(monkeypatch, client_class):
    client_class.calls = []
    monkeypatch.setattr(repo_module.httpx, "AsyncClient", client_class)
    monkeypatch.setattr(repo_module.httpx, "Timeout", lambda *a, **k: None)
    return client_class.calls


# ------------------------------------------- API-11: verify of a hidden node


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_status", [403, 404])
async def test_verify_names_a_node_the_repository_hides_or_lacks(
    monkeypatch, repo_status
):
    class _Hidden(_Repository):
        async def get(self, url, headers=None):
            return _Response(repo_status, {"message": "no access"})

    _use(monkeypatch, _Hidden)

    result = await RepositoryService("u", "p").verify_node(NODE_ID)

    assert result["success"] is False
    assert result.get("error_code") == "node_not_found"


def test_verify_answers_404_for_a_hidden_node(monkeypatch):
    class _Hidden(_Repository):
        async def get(self, url, headers=None):
            return _Response(403, {"message": "no access"})

    _use(monkeypatch, _Hidden)
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )

    with TestClient(main.app) as client:
        response = client.post(f"/upload/verify/{NODE_ID}")

    assert response.status_code == 404, response.text


# ------------------------------------------------ API-10: verify 422 detail


def test_verify_names_the_refused_field_not_the_raw_error(monkeypatch):
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )

    with TestClient(main.app) as client:
        response = client.post(
            f"/upload/verify/{NODE_ID}",
            json={"expected_metadata": "secret-value-xyz"},
        )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "expected_metadata" in detail
    assert "secret-value-xyz" not in detail
    assert "errors.pydantic.dev" not in detail


# --------------------------------------------------- API-12: /workflow


def test_workflow_of_an_unknown_node_is_a_404_and_sets_nothing(monkeypatch):
    class _Missing(_Repository):
        async def get(self, url, headers=None):
            type(self).calls.append(("GET", url))
            return _Response(404, {"message": "not found"})

    calls = _use(monkeypatch, _Missing)
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )

    with TestClient(main.app) as client:
        response = client.post(
            f"/workflow/{NODE_ID}", json={"steps": ["140_ELEMENT_LEGALLY_APPROVED"]}
        )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert not [url for method, url in calls if method == "PUT"]


def test_workflow_of_a_known_node_still_runs_its_steps(monkeypatch):
    calls = _use(monkeypatch, _Repository)
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )

    with TestClient(main.app) as client:
        response = client.post(
            f"/workflow/{NODE_ID}", json={"steps": ["140_ELEMENT_LEGALLY_APPROVED"]}
        )

    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert [url for method, url in calls if method == "PUT"]


def test_workflow_ends_at_the_upload_deadline_with_504(monkeypatch):
    class _Slow:
        async def set_workflow(self, **kwargs):
            # Bounded, so a missing deadline fails the test instead of hanging it.
            await asyncio.sleep(3)
            return {"success": True, "nodeId": NODE_ID, "steps": []}

    monkeypatch.setattr(deps, "get_repository_service", lambda: _Slow())
    monkeypatch.setattr(deps.settings, "upload_timeout", 0.2)

    with TestClient(main.app) as client:
        response = client.post(
            f"/workflow/{NODE_ID}", json={"steps": ["140_ELEMENT_LEGALLY_APPROVED"]}
        )

    assert response.status_code == 504, response.text
    body = response.json()
    assert body["error"]["code"] == "UPLOAD_TIMEOUT"
    assert body["node"] == {"nodeId": NODE_ID}
    assert NODE_ID in body["detail"]


# ------------------------------- handed-over node, write raised after sending


class _BulkWriteLost(_Repository):
    async def post(self, url, headers=None, json=None, files=None):
        if "METADATA_UPDATE" in url:
            type(self).calls.append(("POST", url))
            raise httpx.ReadTimeout("answer lost")
        return await super().post(url, headers=headers, json=json, files=files)


@pytest.mark.asyncio
async def test_a_handed_over_node_whose_write_raised_is_named_as_partial(
    monkeypatch,
):
    _use(monkeypatch, _BulkWriteLost)

    result = await RepositoryService("u", "p").upload_metadata(
        metadata=dict(METADATA),
        node_id=NODE_ID,
        context="default",
        version="2.0.0",
        write_extended_data=False,
        start_workflow=False,
    )

    assert result["success"] is False
    assert result["node"]["nodeId"] == NODE_ID
    assert "kann teilweise geschrieben sein" in result["error"]
    assert "ReadTimeout" in result["error"]
    assert "error_code" not in result


# --------------------------------------------- aspects after a failed read


@pytest.mark.asyncio
async def test_aspects_are_not_replaced_when_the_current_ones_are_unknown(
    monkeypatch,
):
    class _ReadFails(_Repository):
        async def get(self, url, headers=None):
            type(self).calls.append(("GET", url))
            return _Response(500, {"message": "boom"})

    calls = _use(monkeypatch, _ReadFails)
    service = RepositoryService("u", "p")

    async with httpx.AsyncClient() as client:
        problem = await service._ensure_aspects(
            client, "https://repo", NODE_ID, {"cm:author": "Ada"}
        )

    assert not [url for method, url in calls if method == "PUT"], (
        "a PUT with only the new aspects would drop the node's others"
    )
    assert problem and "cm:author" in problem and "HTTP 500" in problem


# ------------------------------------------------- S-17: preview URL in log


def test_upload_logs_the_preview_url_without_its_query(monkeypatch, caplog):
    class _Capture:
        async def capture(self, url, method=None):
            raise RuntimeError("no browser here")

    class _Service:
        async def write_node(self, job, deadline=None):
            return {"success": True, "node": {"nodeId": NODE_ID}}

    monkeypatch.setattr(deps, "get_repository_service", lambda: _Service())
    monkeypatch.setattr(deps, "get_screenshot_service", lambda: _Capture())

    with caplog.at_level(logging.INFO, logger="src.api.routers.upload"):
        with TestClient(main.app) as client:
            client.post(
                "/upload",
                json={
                    "cclom:title": "Titel",
                    "preview_url": "https://example.org/page?token=secret-token-xyz",
                },
            )

    assert "https://example.org/page" in caplog.text
    assert "secret-token-xyz" not in caplog.text
