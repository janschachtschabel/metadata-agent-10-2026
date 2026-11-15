"""
A write endpoint does not ignore an option it does not have (audit 14.11.,
list R-N).

/nodes took the options of /upload - `check_duplicates`, `workflow_steps`,
`preview_url` ... - without a word: in the flat form they were filed away as
metadata fields, in the nested form dropped, and the node was written as if
they had not been sent. A misspelt option beside `metadata` went the same way
on both endpoints. Such a body is refused with 422 now. What a /generate answer
carries beside its fields, and the ignored `repository`, still pass.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services.repository_support import SAVE_NODE_OPTION_KEYS, UPLOAD_OPTION_KEYS

NODE = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"
RECORD = {"cclom:title": "Titel", "metadataset": "event.json"}
ONLY_UPLOAD = sorted(
    set(UPLOAD_OPTION_KEYS) - set(SAVE_NODE_OPTION_KEYS) - {"repository"}
)
ONLY_NODES = sorted(set(SAVE_NODE_OPTION_KEYS) - set(UPLOAD_OPTION_KEYS))
QUIET_UPLOAD = {"check_duplicates": False, "start_workflow": False}


class _Repository:
    def __init__(self):
        self.calls = []

    async def write_node(self, job, deadline=None):
        self.calls.append(vars(job))
        return {"success": True, "node": {"nodeId": NODE}}


@pytest.fixture
def repository(monkeypatch):
    repository = _Repository()
    monkeypatch.setattr(deps, "get_repository_service", lambda: repository)
    return repository


def _post(path, body):
    with TestClient(main.app) as client:
        return client.post(path, json=body)


def _forms(option):
    """The option in a flat body and beside a nested record."""
    return [{**RECORD, option: True}, {"metadata": RECORD, option: True}]


@pytest.mark.parametrize("option", ONLY_UPLOAD)
def test_nodes_refuses_an_option_only_upload_has(repository, option):
    for body in _forms(option):
        response = _post("/nodes", body)

        assert response.status_code == 422, response.text
        assert option in response.json()["detail"]
        assert "/upload" in response.json()["detail"]
    assert repository.calls == []


@pytest.mark.parametrize("option", ONLY_NODES)
def test_upload_refuses_an_option_only_nodes_has(repository, option):
    for body in _forms(option):
        response = _post("/upload", {**body, **QUIET_UPLOAD})

        assert response.status_code == 422, response.text
        assert option in response.json()["detail"]
        assert "/nodes" in response.json()["detail"]
    assert repository.calls == []


@pytest.mark.parametrize("path", ["/upload", "/nodes"])
def test_an_unknown_key_beside_the_nested_record_is_refused(repository, path):
    response = _post(path, {"metadata": RECORD, "colection_id": [NODE]})

    assert response.status_code == 422, response.text
    assert "colection_id" in response.json()["detail"]
    assert repository.calls == []


@pytest.mark.parametrize("path", ["/upload", "/nodes"])
def test_the_refusal_quotes_a_short_name_only(repository, path):
    response = _post(path, {"metadata": RECORD, "x" * 100_000: 1})

    assert response.status_code == 422
    assert len(response.content) < 2_000


GENERATED = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "event.json",
    "metadataset_uri": "http://w3id.org/openeduhub/vocabs/contentTypes/event",
    "language": "de",
    "exportedAt": "2026-11-14T00:00:00Z",
    "_origins": {"cclom:title": "ai"},
    "_source_text": "Quelle",
    "preview_image_url": "data:image/png;base64,AAAA",
    "processing": {"status": "success"},
    "repository": "staging",
}


@pytest.mark.parametrize("path", ["/upload", "/nodes"])
@pytest.mark.parametrize("nested", [True, False])
def test_what_a_generate_answer_carries_still_passes(repository, path, nested):
    fields = {"cclom:title": "Titel"}
    body = {**GENERATED, "metadata": fields} if nested else {**GENERATED, **fields}
    if path == "/upload":
        body.update(QUIET_UPLOAD)

    response = _post(path, body)

    assert response.status_code == 200, response.text
    assert len(repository.calls) == 1


@pytest.mark.parametrize(
    "path, options",
    [
        ("/upload", {**QUIET_UPLOAD, "source": "Klexikon", "return_full_node": True}),
        ("/nodes", {"write_extended_data": True, "start_review_workflow": True}),
    ],
)
def test_the_own_options_work_in_both_forms(repository, path, options):
    for body in ({**RECORD, **options}, {"metadata": RECORD, **options}):
        assert _post(path, body).status_code == 200

    assert len(repository.calls) == 2


def test_an_unknown_key_in_a_flat_body_stays_a_field(repository):
    # There a key cannot be told from a field the schema does not know: the
    # answer lists it in `skipped_fields`.
    response = _post("/nodes", {**RECORD, "schema:unbekannt": "x"})

    assert response.status_code == 200
    assert repository.calls[0]["metadata"]["schema:unbekannt"] == "x"
