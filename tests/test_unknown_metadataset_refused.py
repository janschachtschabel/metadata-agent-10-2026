"""
A `metadataset` whose schema cannot be loaded is refused before the first
repository request (audit 14.11., R-04).

A typo ('learning_materials.json'), a file the schema version does not have or
a content-type URI that names nothing used to fall back to core.json without a
word: the type's fields were dropped, the answer said `success: true` and named
the unknown schema in `schema_used`. Now: 422 SCHEMA_UNAVAILABLE, step
`schema`, nothing written.
"""

import pytest
from fastapi.testclient import TestClient

from repository_fake import FakeRepository, install
from src import main
from src.api import deps
from src.services.repository_service import NodeWrite, RepositoryService

METADATA = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
    # No URL: no duplicate check, no screenshot next to /upload.
    "cclom:title": "Bruchrechnung Klasse 6",
    "ccm:price": "http://w3id.org/openeduhub/vocabs/price/no",
}

UNLOADABLE = [
    "learning_materials.json",  # typo
    "veranstaltung.json",  # no such file in this version
    "http://w3id.org/openeduhub/vocabs/contentTypes/gibt_es_nicht",
    "../core.json",
]


async def _write(metadataset):
    job = NodeWrite(
        metadata={**METADATA, "metadataset": metadataset},
        context="default",
        version="2.0.0",
        workflow_steps=["200_tocheck"],
    )
    return await RepositoryService("user", "password").write_node(job)


@pytest.mark.parametrize("metadataset", UNLOADABLE)
async def test_an_unloadable_metadataset_writes_nothing(monkeypatch, metadataset):
    calls = install(monkeypatch)

    result = await _write(metadataset)

    assert calls == [], "refused before the first repository request"
    assert result["success"] is False
    assert result["error_code"] == "schema_unavailable"
    assert result["step"] == "schema"
    assert "metadataset" in result["error"]


async def test_the_refusal_names_the_schema_and_the_version(monkeypatch):
    install(monkeypatch)

    result = await _write("learning_materials.json")

    assert "learning_materials.json" in result["error"]
    assert "2.0.0" in result["error"]


@pytest.mark.parametrize(
    "metadataset",
    [
        "learning_material.json",
        "http://w3id.org/openeduhub/vocabs/contentTypes/learning_material",
    ],
)
async def test_a_schema_named_by_file_or_uri_is_still_written(monkeypatch, metadataset):
    install(monkeypatch)

    result = await _write(metadataset)

    assert result["success"] is True, result
    assert result["schema_used"] == "learning_material.json"
    assert result["repo_fields_available"] == 40


@pytest.mark.parametrize("path", ["/upload", "/nodes"])
def test_the_routes_answer_422_schema_unavailable(monkeypatch, path):
    calls = install(monkeypatch, FakeRepository)
    monkeypatch.setattr(
        deps, "get_repository_service", lambda: RepositoryService("u", "p")
    )

    with TestClient(main.app) as client:
        response = client.post(
            path, json={**METADATA, "metadataset": "learning_materials.json"}
        )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "SCHEMA_UNAVAILABLE"
    assert body["step"] == "schema"
    assert "learning_materials.json" in body["detail"]
    assert calls == []
