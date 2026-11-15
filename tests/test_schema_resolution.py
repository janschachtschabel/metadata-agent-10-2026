"""
Context, version and schema are resolved one way on every endpoint (audit
14.11., S-37, S-38, K-08, K-09, Q-08a).

Five copies of "resolve latest" had drifted apart: /validate answered an
unknown context with 200 `valid: false`, /export/markdown with 200 and a
document "Schema not found", /generate with 404; /detect-content-type named
"version '1.8.1'" for a context that does not exist; `"schemaVersion": null`
was the default on /validate and "Version 'None'" on /upload; "vvv2.0.0" passed
as a version. Now: unknown context, version or schema file is a 404 that says
which, null and "latest" are the context's default, one leading "v" is allowed.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.utils import schema_loader

RECORD = {"cclom:title": "Titel"}
FIELD = {"schema_file": "event.json", "field_id": "cclom:title"}
NODE = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"


@pytest.fixture
def client(monkeypatch):
    def not_reached(*args, **kwargs):
        raise AssertionError("refused too late: a service was asked for")

    for name in ("get_llm_service", "get_input_source_service"):
        monkeypatch.setattr(deps, name, not_reached)
    with TestClient(main.app, raise_server_exceptions=False) as client:
        yield client


# ------------------------------------------------------------ schema_loader


def test_latest_and_none_are_the_default_version_of_the_context():
    for version in ("latest", "LATEST", None):
        assert schema_loader.resolve_version("default", version) == "2.0.0"


def test_one_leading_v_is_allowed():
    assert schema_loader.resolve_version("default", "v2.0.0") == "2.0.0"
    assert schema_loader.resolve_version("default", "1.8.1") == "1.8.1"


@pytest.mark.parametrize(
    "version", ["vv2.0.0", "vvv2.0.0", "v", "2.0", "../x", "v2", ""]
)
def test_anything_else_is_no_version(version):
    with pytest.raises(ValueError, match="Invalid schema version"):
        schema_loader.resolve_version("default", version)
    with pytest.raises(ValueError, match="Invalid schema version"):
        schema_loader.load_schema("default", version, "core.json")


@pytest.mark.parametrize(
    "call",
    [
        lambda: schema_loader.get_latest_version("nope"),
        lambda: schema_loader.resolve_version("nope", "latest"),
        lambda: schema_loader.resolve_version("nope", "2.0.0"),
        lambda: schema_loader.load_schema("nope", "latest", "core.json"),
        lambda: schema_loader.get_available_schemas("nope", "latest"),
    ],
)
def test_no_version_is_invented_for_an_unknown_context(call, caplog):
    with pytest.raises(ValueError, match="Unknown context: nope"):
        call()

    assert "1.8.1" not in caplog.text


# ------------------------------------------------------------ the endpoints

UNKNOWN = [
    ({"context": "nope"}, "Unknown context: nope"),
    ({"version": "9.9.9"}, "9.9.9"),
    ({"version": "vvv2.0.0"}, "Invalid schema version"),
    ({"schema_file": "nope.json"}, "Schema not found: nope.json"),
    ({"schema_file": "../../x.json"}, "Invalid schema file name"),
]
ENDPOINTS = {
    "/generate": {"text": "abc"},
    "/extract-field": {"text": "abc", **FIELD},
    "/validate": {"metadata": RECORD},
    "/export/markdown": {"metadata": RECORD},
}


@pytest.mark.parametrize("path", sorted(ENDPOINTS))
@pytest.mark.parametrize("option, message", UNKNOWN)
def test_an_unknown_context_version_or_schema_is_a_404(client, path, option, message):
    response = client.post(path, json={**ENDPOINTS[path], **option})

    assert response.status_code == 404, response.text
    assert message in response.json()["detail"]
    assert "1.8.1" not in response.json()["detail"]


@pytest.mark.parametrize("path", ["/validate", "/export/markdown"])
@pytest.mark.parametrize(
    "marker, message",
    [
        ({"contextName": "nope"}, "Unknown context: nope"),
        ({"schemaVersion": "9.9.9"}, "9.9.9"),
        ({"metadataset": "nope.json"}, "Schema not found: nope.json"),
        ({"_schema": {"context": "nope"}}, "Unknown context: nope"),
    ],
)
@pytest.mark.parametrize("nested", [True, False])
def test_unknown_markers_inside_the_record_are_a_404_too(
    client, path, marker, message, nested
):
    record = {**RECORD, **marker}

    response = client.post(path, json={"metadata": record} if nested else record)

    assert response.status_code == 404, response.text
    assert message in response.json()["detail"]


@pytest.mark.parametrize(
    "option, message",
    [
        ({"context": "nope"}, "Unknown context: nope"),
        ({"version": "vvv2.0.0"}, "Invalid schema version"),
        ({"version": "9.9.9"}, "9.9.9"),
    ],
)
def test_content_type_detection_names_what_is_unknown(client, option, message):
    response = client.post("/detect-content-type", json={"text": "abc", **option})

    assert response.status_code == 404, response.text
    assert message in response.json()["detail"]
    assert "1.8.1" not in response.json()["detail"]


@pytest.mark.parametrize(
    "path", ["/info/schemas/nope/latest", "/info/schemas/nope/2.0.0"]
)
def test_the_schema_list_names_an_unknown_context(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown context: nope"


@pytest.mark.parametrize("version", ["latest", "v2.0.0", "2.0.0", None, ""])
def test_validate_takes_every_spelling_of_the_default_version(client, version):
    response = client.post(
        "/validate", json={"metadata": {**RECORD, "schemaVersion": version}}
    )

    assert response.status_code == 200, response.text
    assert response.json()["schema_used"] == "learning_material.json"


def test_the_export_names_the_schema_it_used(client):
    for record, used in (
        (RECORD, "learning_material.json"),
        ({**RECORD, "metadataset": "event.json"}, "event.json"),
        (
            {
                **RECORD,
                "metadataset": "http://w3id.org/openeduhub/vocabs/contentTypes/event",
            },
            "event.json",
        ),
    ):
        response = client.post("/export/markdown", json=record)

        assert response.status_code == 200, response.text
        assert response.json()["schema_used"] == used


def test_an_unknown_schema_uri_stays_a_400(client):
    for path in ("/validate", "/export/markdown", "/generate"):
        body = {**ENDPOINTS[path], "schema_file": "https://example.org/nope"}

        assert client.post(path, json=body).status_code == 400


# ------------------------------------------------------------ null markers


class _Repository:
    """Records context and version of a write."""

    def __init__(self):
        self.calls = []

    async def write_node(self, job, deadline=None):
        self.calls.append((job.context, job.version))
        return {"success": True, "node": {"nodeId": NODE}}

    async def verify_node(self, **kwargs):
        self.calls.append((kwargs["context"], kwargs["version"]))
        return {"success": True, "actual_metadata": {}}


@pytest.mark.parametrize(
    "path, body",
    [
        ("/upload", {**RECORD, "check_duplicates": False, "start_workflow": False}),
        ("/nodes", RECORD),
        (f"/upload/verify/{NODE}", {"expected_metadata": RECORD}),
    ],
)
@pytest.mark.parametrize("markers", [{"schemaVersion": None, "contextName": None}, {}])
def test_null_markers_are_the_defaults_on_the_write_endpoints(
    client, monkeypatch, path, body, markers
):
    repository = _Repository()
    monkeypatch.setattr(deps, "get_repository_service", lambda: repository)
    if "expected_metadata" in body:
        body = {"expected_metadata": {**body["expected_metadata"], **markers}}
    else:
        body = {**body, **markers}

    response = client.post(path, json=body)

    assert response.status_code == 200, response.text
    assert repository.calls == [("default", "latest")]
