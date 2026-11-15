"""
What /docs says is what the endpoints do (audit 14.11., Q-06, Q-11, Q-17).

The text of /upload listed the write steps in the order before the collections
moved behind the preview; `GET /info/schemas/{context}/{version}` had no test
and described `groups` as a number and a key `metadata` nobody returns; the AI
endpoints named the ignored `repository` as a field a node needs.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.services.repository_diff import WRITTEN_AS, compute_diff


@pytest.fixture(scope="module")
def spec() -> dict:
    return main.app.openapi()


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as client:
        yield client


def _description(spec, path, method="post") -> str:
    return spec["paths"][path][method]["description"]


def test_upload_lists_its_steps_in_the_order_they_run(spec):
    text = _description(spec, "/upload")
    steps = [
        "**Duplikat-Check**",
        "**Node erstellen**",
        "**Metadaten setzen**",
        "**Extended Fields**",
        "**Vorschaubild**",
        "**Collections**",
        "**Workflow**",
        "**Node zurücklesen**",
    ]

    positions = [text.index(step) for step in steps]

    assert positions == sorted(positions)
    for number, step in enumerate(steps, start=1):
        assert f"{number}. {step}" in text


@pytest.mark.parametrize(
    "path", ["/generate", "/detect-content-type", "/extract-field"]
)
def test_a_node_needs_no_repository_field(spec, path):
    text = _description(spec, path)

    assert "| `node_id` |" in text
    assert "`node_id`, `repository`" not in text
    assert "Deprecated" in text  # the parameter is still explained as ignored


# ------------------------------------------------------------ /info/schemas


def test_the_schema_list_of_a_version(client, spec):
    response = client.get("/info/schemas/default/2.0.0")

    assert response.status_code == 200
    schemas = {schema["file"]: schema for schema in response.json()}
    event = schemas["event.json"]
    assert set(event) == {"file", "profile_id", "label", "groups", "field_count"}
    assert event["profile_id"] == "type:event"
    assert event["label"] == {"de": "Veranstaltung", "en": "Event"}
    assert "schedule" in event["groups"]
    assert all(isinstance(group, str) for group in event["groups"])
    assert event["field_count"] > 0
    assert "core.json" in schemas
    # The text says what the fields hold.
    text = _description(spec, "/info/schemas/{context}/{version}", "get")
    assert "**groups**: IDs der Feldgruppen" in text
    assert "Anzahl Feldgruppen" not in text


def test_latest_is_the_default_version(client):
    latest = client.get("/info/schemas/default/latest").json()

    assert latest == client.get("/info/schemas/default/v2.0.0").json()
    assert latest == client.get("/info/schemas/default/2.0.0").json()


@pytest.mark.parametrize(
    "path, message",
    [
        ("/info/schemas/nope/latest", "Unknown context: nope"),
        ("/info/schemas/default/9.9.9", "Unknown version: 9.9.9"),
        ("/info/schemas/default/abc", "Invalid schema version"),
    ],
)
def test_an_unknown_context_or_version_of_the_list_is_a_404(client, path, message):
    response = client.get(path)

    assert response.status_code == 404
    assert message in response.json()["detail"]
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------------ /info/schema


def test_a_schema_definition_is_the_file_as_shipped(client, spec):
    response = client.get("/info/schema/default/latest/event.json")

    assert response.status_code == 200
    schema = response.json()
    text = _description(spec, "/info/schema/{context}/{version}/{schema_file}", "get")
    for key in schema:
        assert f"**{key}**" in text, key
    assert "**metadata**" not in text
    assert schema["profileId"] == "type:event"
    assert {field["id"] for field in schema["fields"]} >= {"ccm:oeh_event_begin"}


@pytest.mark.parametrize(
    "path, message",
    [
        ("/info/schema/nope/latest/core.json", "Unknown context: nope"),
        ("/info/schema/default/9.9.9/core.json", "Schema not found: core.json"),
        ("/info/schema/default/latest/nope.json", "Schema not found: nope.json"),
        ("/info/schema/default/latest/core", "Invalid schema file name"),
    ],
)
def test_an_unknown_schema_definition_is_a_404(client, path, message):
    response = client.get(path)

    assert response.status_code == 404
    assert message in response.json()["detail"]


# ------------------------------------------------------------ /upload/verify


def _status_row(text: str, status: str) -> str:
    (row,) = [line for line in text.splitlines() if line.startswith(f"| `{status}` |")]
    return row


def test_verify_explains_not_written_by_the_repository_fields_of_the_schema(spec):
    """
    The text said "virtual:, schema:, oder kein repo_field". A `schema:` field
    is written where the schema declares it a repository field; what decides is
    the schema, and a renamed field counts under the name it is written as.
    """
    text = _description(spec, "/upload/verify/{node_id}")
    row = _status_row(text, "not_written")

    assert "schema:" not in row
    assert "`virtual:`" in row and "Repository-Feld" in row
    for sent_as, written_as in WRITTEN_AS.items():
        assert f"`{sent_as}`" in row and f"`{written_as}`" in row


def test_verify_says_how_values_are_compared(spec):
    text = _description(spec, "/upload/verify/{node_id}")
    row = _status_row(text, "match")

    # By field kind: case counts in free text only.
    assert "Vokabular" in row and "Freitext" in row
    # A licence link is stored as key and version, an author by name.
    assert "`ccm:custom_license`" in row and "`ccm:commonlicense_key`" in row
    assert "`cm:author`" in row
    # Every form /upload takes; its options are no fields.
    assert "`metadata.metadata`" in text
    assert "Optionen von `/upload` und `/nodes`" in text


def test_every_status_the_comparison_reports_has_a_row(spec):
    text = _description(spec, "/upload/verify/{node_id}")
    _, summary = compute_diff({"cclom:title": "T"}, {}, "default", "2.0.0")

    for status in summary:
        assert f"`{status}`" in text, status
