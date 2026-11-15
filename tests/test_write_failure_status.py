"""
A write that left nothing behind is no 200 (audit 18.10. final, API-08).

/upload and /nodes answered a complete failure - nothing created, nothing
changed - with 200 and success:false, while POST /node answered the same failed
create with 502. Now:

- the body breaks the schema's object shapes  → 422 VALIDATION_ERROR
- the schema cannot be loaded (context/version) → 422 SCHEMA_UNAVAILABLE
- the repository refused or failed             → 502 UPSTREAM_ERROR

`detail` keeps the text `error` had (the 2.1.1 widget shows error.error.detail),
`error.message` the same (the 3.0.0 widget shows that). A node this call
created and discarded is named in `discarded_node`, one it could not discard
in `node`. A partial result - the node exists with what was written - and a
duplicate stay 200 with success:false.
"""

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
OLD_ID = "177fba93-3885-479e-bfba-933885c79e1c"
METADATA = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
    # No URL: no duplicate check, no screenshot next to /upload.
    "cclom:title": "Bruchrechnung Klasse 6",
}
WRITE_PATHS = ["/upload", "/nodes"]


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


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
        created = datetime.now(timezone.utc).isoformat()
        return _Response(
            200, {"node": {"properties": {}, "aspects": [], "createdAt": created}}
        )

    async def delete(self, url, headers=None):
        type(self).calls.append(("DELETE", url))
        return _Response(200, {})


class _CreateRefused(_Repository):
    async def post(self, url, headers=None, json=None, files=None):
        if "/children?" in url:
            type(self).calls.append(("POST", url))
            return _Response(500, {"message": "kaputt"})
        return await super().post(url, headers=headers, json=json, files=files)


class _MetadataRefused(_Repository):
    async def post(self, url, headers=None, json=None, files=None):
        if "/metadata?" in url:
            type(self).calls.append(("POST", url))
            return _Response(400, {"message": "nein"})
        return await super().post(url, headers=headers, json=json, files=files)


class _MetadataUnreachable(_Repository):
    async def post(self, url, headers=None, json=None, files=None):
        if "/metadata?" in url:
            raise httpx.ConnectError("refused by 10.0.0.5")
        return await super().post(url, headers=headers, json=json, files=files)


class _DiscardFails(_MetadataUnreachable):
    async def delete(self, url, headers=None):
        type(self).calls.append(("DELETE", url))
        return _Response(500, {})


@pytest.fixture
def post(monkeypatch):
    def run(path, client_class, body=None):
        client_class.calls = []
        monkeypatch.setattr(repo_module.httpx, "AsyncClient", client_class)
        monkeypatch.setattr(repo_module.httpx, "Timeout", lambda *a, **k: None)
        monkeypatch.setattr(
            deps, "get_repository_service", lambda: RepositoryService("u", "p")
        )
        with TestClient(main.app) as client:
            response = client.post(path, json=body or METADATA)
        response.calls = client_class.calls
        return response

    return run


def _error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code
    assert body["error"]["message"] == body["detail"]
    assert body["error"]["request_id"]
    assert "success" not in body
    return body


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_an_object_shape_violation_is_a_422_and_writes_nothing(post, path):
    body = {
        "contextName": "mds_oeh",
        "schemaVersion": "2.0.0",
        "metadataset": "event.json",
        "cclom:title": "Konzert",
        "schema:organizer": ["TEEKAYART"],
    }

    response = post(path, _Repository, body)

    detail = _error(response, 422, "VALIDATION_ERROR")["detail"]
    assert detail.startswith("Metadaten wurden nicht geschrieben")
    assert "schema:organizer" in detail
    assert response.calls == []


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_an_unloadable_schema_is_a_422_and_writes_nothing(post, path):
    response = post(path, _Repository, {**METADATA, "contextName": "gibt-es-nicht"})

    detail = _error(response, 422, "SCHEMA_UNAVAILABLE")["detail"]
    assert "gibt-es-nicht" in detail
    assert response.calls == []


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_a_refused_create_is_a_502(post, path):
    response = post(path, _CreateRefused)

    body = _error(response, 502, "UPSTREAM_ERROR")
    assert body["detail"] == "Create node failed: HTTP 500: kaputt"
    assert "node" not in body and "discarded_node" not in body


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_refused_metadata_is_a_502_naming_the_discarded_node(post, path):
    response = post(path, _MetadataRefused)

    body = _error(response, 502, "UPSTREAM_ERROR")
    assert body["discarded_node"] == NODE_ID
    assert body["step"] == "setMetadata"
    assert body["field_errors"], "the refused fields stay in the answer"
    assert "verworfen" in body["detail"]


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_an_unreachable_repository_is_a_502_naming_the_type_only(post, path):
    response = post(path, _MetadataUnreachable)

    body = _error(response, 502, "UPSTREAM_ERROR")
    assert "ConnectError" in body["detail"]
    assert "10.0.0.5" not in response.text
    assert body["discarded_node"] == NODE_ID


def test_a_node_that_could_not_be_discarded_is_named_in_the_error(post):
    response = post("/upload", _DiscardFails)

    body = _error(response, 502, "UPSTREAM_ERROR")
    assert body["node"]["nodeId"] == NODE_ID
    assert "liegt weiterhin im Eingangsordner" in body["detail"]


def test_a_refused_write_into_a_handed_over_node_is_a_502(post):
    response = post("/nodes", _MetadataRefused, {**METADATA, "node_id": OLD_ID})

    body = _error(response, 502, "UPSTREAM_ERROR")
    assert "discarded_node" not in body
    assert not [url for method, url in response.calls if method == "DELETE"]


# ------------------------------------------------------- still 200


class _Service:
    def __init__(self, result):
        self.result = result

    async def write_node(self, job, deadline=None):
        return dict(self.result)


@pytest.mark.parametrize("path", WRITE_PATHS)
def test_a_partial_result_stays_a_200(monkeypatch, path):
    kept = {
        "success": False,
        "error": f"Timeout — Node {NODE_ID} ist bereits geschrieben und wurde "
        "nicht verworfen",
        "node": {"nodeId": NODE_ID},
    }
    monkeypatch.setattr(deps, "get_repository_service", lambda: _Service(kept))

    with TestClient(main.app) as client:
        response = client.post(path, json={"cclom:title": "Titel"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is False
    assert body["node"]["nodeId"] == NODE_ID
    assert body["error"] == kept["error"]


def test_a_handed_over_node_whose_write_raised_stays_a_200(post):
    class _BulkWriteLost(_Repository):
        async def post(self, url, headers=None, json=None, files=None):
            if "METADATA_UPDATE" in url:
                raise httpx.ReadTimeout("answer lost")
            return await super().post(url, headers=headers, json=json, files=files)

    response = post("/nodes", _BulkWriteLost, {**METADATA, "node_id": OLD_ID})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is False
    assert body["node"]["nodeId"] == OLD_ID
    assert "kann teilweise geschrieben sein" in body["error"]


@pytest.mark.parametrize(
    "path, words",
    [
        ("/upload", ["SCHEMA_UNAVAILABLE", "UPSTREAM_ERROR", "discarded_node"]),
        ("/nodes", ["SCHEMA_UNAVAILABLE", "UPSTREAM_ERROR", "discarded_node"]),
        ("/workflow/{node_id}", ["`404`", "UPLOAD_TIMEOUT"]),
        ("/upload/verify/{node_id}", ["`404`", "`403`", "`422`"]),
    ],
)
def test_the_openapi_description_names_the_status_codes(path, words):
    description = main.app.openapi()["paths"][path]["post"]["description"]

    for word in words:
        assert word in description, (path, word)
