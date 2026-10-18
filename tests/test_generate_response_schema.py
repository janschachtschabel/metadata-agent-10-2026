"""
The documented /generate response is the one that is sent (audit API-09).

The OpenAPI schema declared a nested `metadata` object and a ProcessingInfo
without `status` and `error`, while the body carries the metadata fields at top
level next to `_origins`, `_source_text` and `preview_image_url`. Only the
documentation changes: the body is pinned below exactly as it was.
"""

from fastapi.testclient import TestClient

from src import main

from test_generate_failures import _Gateway, stack  # noqa: F401 - fixture


def _schemas():
    return main.app.openapi()["components"]["schemas"]


def _generate_schema():
    spec = main.app.openapi()
    ok = spec["paths"]["/generate"]["post"]["responses"]["200"]
    ref = ok["content"]["application/json"]["schema"]["$ref"]
    return _schemas()[ref.rsplit("/", 1)[-1]]


def test_the_envelope_is_flat_and_open_for_the_metadata_fields():
    schema = _generate_schema()

    assert "metadata" not in schema["properties"]
    assert schema.get("additionalProperties") is True
    for name in ("_origins", "_source_text", "preview_image_url", "processing"):
        assert name in schema["properties"], name


def test_processing_documents_status_and_error():
    processing = _schemas()["ProcessingInfo"]["properties"]

    for name in (
        "status",
        "success",
        "fields_extracted",
        "fields_failed",
        "fields_total",
        "auth_source",
        "content_type_detection",
        "error",
        "errors",
        "warnings",
    ):
        assert name in processing, name


def test_the_body_is_unchanged(stack):  # noqa: F811
    stack(_Gateway(content='{"value": "Titel aus der Seite"}'))

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={
                "text": "Workshop am 15. März 2025 in Berlin.",
                "schema_file": "event.json",
                "enable_geocoding": False,
                "existing_metadata": {"_origins": {"cclom:title": "user"}},
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    keys = list(body)
    assert keys[:6] == [
        "contextName",
        "schemaVersion",
        "metadataset",
        "metadataset_uri",
        "language",
        "exportedAt",
    ]
    assert keys[-3:] == ["_origins", "_source_text", "processing"]
    assert "metadata" not in body
    assert body["cclom:title"] == "Titel aus der Seite"
    assert body["processing"]["status"] == "success"
    assert set(body["processing"]) >= {
        "status",
        "success",
        "fields_extracted",
        "fields_failed",
        "fields_total",
        "processing_time_ms",
        "llm_provider",
        "llm_model",
        "auth_source",
        "content_type_detection",
        "errors",
        "warnings",
    }
