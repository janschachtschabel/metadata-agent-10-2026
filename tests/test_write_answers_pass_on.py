"""
What the write endpoints pass on from the repository service (audit 14.11.,
follow-ups of R-01, R-04, R-07, R-13 in the API layer).

- A deadline that hits after the first collection request keeps the node: the
  504 names it in `node` and says in `collections` which links were made.
- `skipped_fields` - the sent fields that reached no repository property - was
  filtered out by the response models.
- The options a flat body carries are one list, shared with the comparison of
  /upload/verify (repository_support).
- The texts under /docs: an unloadable `metadataset` is 422 SCHEMA_UNAVAILABLE,
  /upload/verify compares nested and flat bodies alike.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.api.routers import save_node as save_node_router
from src.api.routers import upload as upload_router
from src.models.schemas import SaveNodeRequest, UploadRequest
from src.services.repository_support import SAVE_NODE_OPTION_KEYS, UPLOAD_OPTION_KEYS

NODE = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"
COLLECTION = "0b1c2d3e-4f50-4617-8899-aabbccddeeff"
OTHER = "1b1c2d3e-4f50-4617-8899-aabbccddeeff"
RECORD = {"cclom:title": "Titel", "metadataset": "event.json"}


class _Repository:
    def __init__(self, result):
        self.result, self.calls = result, []

    async def write_node(self, job, deadline=None):
        self.calls.append(vars(job))
        return self.result


@pytest.fixture
def write(monkeypatch):
    def post(path, result, **body):
        repository = _Repository(result)
        monkeypatch.setattr(deps, "get_repository_service", lambda: repository)
        with TestClient(main.app) as client:
            response = client.post(path, json={**RECORD, **body})
        return response, repository.calls

    return post


def _for_path(path, body):
    """/upload would capture a preview and check duplicates: not here."""
    if path == "/upload":
        return {"check_duplicates": False, "start_workflow": False, **body}
    return body


# ------------------------------------------------------------ 504 with collections

LINKS = [
    {"collectionId": COLLECTION, "success": True},
    {"collectionId": OTHER, "success": False, "error": "nicht mehr versucht"},
]


@pytest.mark.parametrize("path", ["/upload", "/nodes"])
def test_a_deadline_during_the_collections_names_node_and_links(write, path):
    result = {
        "success": False,
        "error_code": "upload_timeout",
        "error": "Zeitlimit erreicht; das Verknüpfen der Sammlungen wurde unterbrochen",
        "node": {"nodeId": NODE},
        "collections": LINKS,
    }

    response, _ = write(path, result, **_for_path(path, {}))

    assert response.status_code == 504, response.text
    answer = response.json()
    assert answer["error"]["code"] == "UPLOAD_TIMEOUT"
    assert answer["node"] == {"nodeId": NODE}
    assert answer["collections"] == LINKS
    assert "discarded_node" not in answer


@pytest.mark.parametrize("path", ["/upload", "/nodes"])
def test_a_deadline_before_the_collections_has_no_such_key(write, path):
    result = {
        "success": False,
        "error_code": "upload_timeout",
        "error": "Zeitlimit erreicht",
        "discarded_node": NODE,
    }

    response, _ = write(path, result, **_for_path(path, {}))

    assert response.status_code == 504
    assert response.json()["discarded_node"] == NODE
    assert "collections" not in response.json()


def test_the_error_schema_describes_the_key():
    properties = main.app.openapi()["components"]["schemas"]["ErrorResponse"][
        "properties"
    ]

    assert "UPLOAD_TIMEOUT" in properties["collections"]["description"]


# ------------------------------------------------------------ skipped_fields


@pytest.mark.parametrize("path", ["/upload", "/nodes"])
def test_the_names_of_skipped_fields_reach_the_caller(write, path):
    result = {
        "success": True,
        "node": {"nodeId": NODE},
        "fields_written": 3,
        "fields_skipped": 2,
        "skipped_fields": ["schema:unbekannt", "virtual:x"],
    }

    response, _ = write(path, result, **_for_path(path, {}))

    assert response.status_code == 200, response.text
    assert response.json()["fields_skipped"] == 2
    assert response.json()["skipped_fields"] == ["schema:unbekannt", "virtual:x"]


# ------------------------------------------------------------ one list of options


def test_the_routes_lift_out_the_shared_lists():
    assert tuple(upload_router._UPLOAD_OPTIONS) == UPLOAD_OPTION_KEYS
    assert tuple(save_node_router._SAVE_NODE_OPTIONS) == SAVE_NODE_OPTION_KEYS
    assert set(UPLOAD_OPTION_KEYS) == set(UploadRequest.model_fields) - {"metadata"}
    assert set(SAVE_NODE_OPTION_KEYS) == set(SaveNodeRequest.model_fields) - {
        "metadata"
    }


VALUES = {
    "node_id": NODE,
    "repository": "prod",
    "check_duplicates": False,
    "start_workflow": False,
    "source": "Klexikon",
    "preview_url": None,
    "screenshot_method": "pageshot",
    "write_extended_data": False,
    "extended_text": "Rohtext",
    "return_full_node": True,
    "collection_id": [COLLECTION],
    "workflow_steps": ["200_tocheck"],
    "workflow_comment": "geprüft",
    "workflow_receiver": ["GROUP_X"],
    "preview": "https://example.org/bild.png",
    "start_review_workflow": True,
    "start_quality_workflow": True,
}


@pytest.mark.parametrize(
    "path, option",
    [("/upload", key) for key in UPLOAD_OPTION_KEYS]
    + [("/nodes", key) for key in SAVE_NODE_OPTION_KEYS],
)
def test_an_option_of_a_flat_body_is_no_metadata_field(write, path, option):
    body = _for_path(path, {})
    body[option] = VALUES[option]

    response, calls = write(path, {"success": True, "node": {"nodeId": NODE}}, **body)

    assert response.status_code == 200, response.text
    (call,) = calls
    assert option not in call["metadata"]
    assert call["metadata"]["cclom:title"] == "Titel"


# ------------------------------------------------------------ the texts


def _description(path) -> str:
    return main.app.openapi()["paths"][path]["post"]["description"]


@pytest.mark.parametrize("path", ["/upload", "/nodes"])
def test_an_unloadable_metadataset_is_documented_as_schema_unavailable(path):
    lines = [
        line for line in _description(path).splitlines() if "SCHEMA_UNAVAILABLE" in line
    ]

    assert lines
    assert any("`metadataset`" in line and "schema" in line for line in lines)


def test_verify_documents_that_both_body_forms_compare_alike():
    text = _description("/upload/verify/{node_id}")

    assert "verschachtelt" in text and "flach" in text
