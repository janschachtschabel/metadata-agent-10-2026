"""
The options of /validate and /export/markdown work in the flat form too.

Audit 13.11., API-15: both endpoints recommend sending the output of
/generate as it is. Options next to its fields - include_empty, context,
version, schema_file - were then taken for metadata fields and ignored
without a word.
"""

from fastapi.testclient import TestClient

from src import main

RECORD = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "event.json",
    "cclom:title": "Workshop",
}


def test_include_empty_works_next_to_the_fields():
    with TestClient(main.app) as client:
        without = client.post("/export/markdown", json=RECORD).json()["markdown"]
        flat = client.post(
            "/export/markdown", json={**RECORD, "include_empty": True}
        ).json()["markdown"]
        nested = client.post(
            "/export/markdown", json={"metadata": RECORD, "include_empty": True}
        ).json()["markdown"]

    assert len(flat) > len(without)
    assert flat == nested
    # An option is not a field of the record.
    assert "include_empty" not in flat


def test_schema_file_next_to_the_fields_selects_the_schema():
    record = {"cclom:title": "Workshop"}

    with TestClient(main.app) as client:
        flat = client.post(
            "/validate",
            json={**record, "schema_file": "event.json", "version": "2.0.0"},
        )
        nested = client.post(
            "/validate",
            json={"metadata": record, "schema_file": "event.json", "version": "2.0.0"},
        )

    assert flat.status_code == nested.status_code == 200
    assert flat.json()["schema_used"] == nested.json()["schema_used"]
    assert "event.json" in flat.json()["schema_used"]
    assert not [e for e in flat.json()["errors"] if e["field_id"] == "schema_file"]


def test_the_spec_lists_the_options_of_both_endpoints():
    """They stood as {"type": "object"}: the Schema tab of /docs listed nothing."""
    with TestClient(main.app) as client:
        paths = client.get("/openapi.json").json()["paths"]

    def body(path):
        content = paths[path]["post"]["requestBody"]["content"]
        return content["application/json"]["schema"]

    for option in ("metadata", "context", "version", "schema_file"):
        assert option in body("/validate")["properties"], option
        assert option in body("/export/markdown")["properties"], option
    assert "include_empty" in body("/export/markdown")["properties"]
    # The flat form: nothing is required, the fields of the record stand beside.
    assert "required" not in body("/validate")
    assert body("/validate")["additionalProperties"] is True
