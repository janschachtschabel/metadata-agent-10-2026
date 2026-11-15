"""
/upload and /nodes turn the same service result into their answers (audit 18.10.
final, architecture finding).

Both routers mapped the untyped result dict on their own. These pin, key by
key, what each answer holds for a full result, so one shared mapping can
replace the two without a caller noticing.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps

NODE_ID = "5ab4b434-4832-45ca-b4b4-34483265ca5d"
COLLECTION_ID = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"

FULL_RESULT = {
    "success": True,
    "schema_used": "learning_material.json",
    "repo_fields_available": 34,
    "node_created": True,
    "node": {
        "nodeId": NODE_ID,
        "title": "Titel",
        "description": "Beschreibung",
        "wwwurl": "https://example.org",
        "repositoryUrl": f"https://repo/components/render/{NODE_ID}",
    },
    "fields_written": 12,
    "fields_skipped": 2,
    "skipped_fields": ["schema:unbekannt", "virtual:x"],
    "collections": [{"collectionId": COLLECTION_ID, "success": False, "error": "x"}],
    "workflow": [
        {"status": "200_tocheck", "success": True},
        {"status": "140_ELEMENT_LEGALLY_APPROVED", "success": False, "error": "e"},
        {"status": "150_PUBLISH_IN_SEARCH", "success": False, "skipped": True},
    ],
    "preview": {"success": False, "error": "kein Bild"},
    "field_errors": [{"field_id": "ccm:x", "error": "bad", "status_code": 400}],
    "error": "1 Feld(er) konnten nicht geschrieben werden",
    "node_full": {"ref": {"id": NODE_ID}},
}

EXPECTED_COMMON = {
    "success": True,
    "node": FULL_RESULT["node"],
    "node_full": {"ref": {"id": NODE_ID}},
    "error": "1 Feld(er) konnten nicht geschrieben werden",
    "step": None,
    "fields_written": 12,
    "fields_skipped": 2,
    "skipped_fields": ["schema:unbekannt", "virtual:x"],
    "node_created": True,
    "schema_used": "learning_material.json",
    "repo_fields_available": 34,
    "field_errors": [{"field_id": "ccm:x", "error": "bad", "status_code": 400}],
    "preview": {"success": False, "error": "kein Bild"},
    "collections": [{"collectionId": COLLECTION_ID, "success": False, "error": "x"}],
    "workflow": [
        {"status": "200_tocheck", "success": True, "error": None, "skipped": None},
        {
            "status": "140_ELEMENT_LEGALLY_APPROVED",
            "success": False,
            "error": "e",
            "skipped": None,
        },
        {
            "status": "150_PUBLISH_IN_SEARCH",
            "success": False,
            "error": None,
            "skipped": True,
        },
    ],
    "discarded_node": None,
}


class _Service:
    def __init__(self, result):
        self.result = result

    async def write_node(self, job, deadline=None):
        return dict(self.result)


@pytest.fixture
def answer(monkeypatch):
    def post(path, result, body=None):
        monkeypatch.setattr(deps, "get_repository_service", lambda: _Service(result))
        with TestClient(main.app) as client:
            return client.post(path, json=body or {"cclom:title": "Titel"})

    return post


def test_upload_maps_every_key_of_a_full_result(answer):
    response = answer("/upload", FULL_RESULT)

    assert response.status_code == 200, response.text
    assert response.json() == {**EXPECTED_COMMON, "duplicate": None}


def test_nodes_maps_every_key_of_a_full_result(answer):
    response = answer("/nodes", FULL_RESULT)

    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_COMMON


def test_a_duplicate_stays_a_200_with_the_existing_node(answer):
    duplicate = {
        "success": False,
        "duplicate": True,
        "node": {"nodeId": NODE_ID, "title": "Alt", "wwwurl": "https://e.org"},
        "error": 'URL existiert bereits: "Alt"',
    }

    response = answer("/upload", duplicate)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is False
    assert body["duplicate"] is True
    assert body["node"]["nodeId"] == NODE_ID
    assert body["error"] == 'URL existiert bereits: "Alt"'


@pytest.mark.parametrize("path", ["/upload", "/nodes"])
def test_a_minimal_result_leaves_the_optional_keys_empty(answer, path):
    response = answer(path, {"success": True, "node": {"nodeId": NODE_ID}})

    body = response.json()
    assert body["node"] == {
        "nodeId": NODE_ID,
        "title": None,
        "description": None,
        "wwwurl": None,
        "repositoryUrl": None,
    }
    for key in ("collections", "workflow", "field_errors", "preview", "node_full"):
        assert body[key] is None, key
