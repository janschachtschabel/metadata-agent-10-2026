"""
`fields_skipped` counts the sent fields that are not written as repository
properties, and the answer names them (audit 14.11., R-13).

UPLOAD-RESPONSE.md promises "Übersprungene Felder (nicht repo-fähig)"; the
count was 0 on every successful write, whatever the repo-field filter dropped.
A field a later step writes under another name (author, licence link, old LRT
name, coordinates, collections) is not skipped, and neither is an empty one.
"""

from repository_fake import FakeRepository, Response, install
from src.services.repository_result import WriteResult
from src.services.repository_service import RepositoryService

COLLECTION = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"
METADATA = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "learning_material.json",
    "cclom:title": "Bruchrechnung Klasse 6",
    "ccm:wwwurl": "https://example.org/bruchrechnung",
}


async def _upload(fields, client_class=FakeRepository, monkeypatch=None):
    install(monkeypatch, client_class)
    return await RepositoryService("user", "password").upload_metadata(
        metadata={**METADATA, **fields},
        check_duplicates=False,
        context="default",
        version="2.0.0",
        write_extended_data=False,
    )


async def test_the_fields_the_filter_dropped_are_counted_and_named(monkeypatch):
    result = await _upload(
        {
            "ccm:erfundenes_feld": "wert",
            "schema:startDate": "2026-03-15",  # an event field, no learning material
            "virtual:irgendwas": "x",
        },
        monkeypatch=monkeypatch,
    )

    assert result["success"] is True
    assert result["fields_written"] == 2
    assert result["fields_skipped"] == 3
    assert result["skipped_fields"] == [
        "ccm:erfundenes_feld",
        "schema:startDate",
        "virtual:irgendwas",
    ]


async def test_nothing_skipped_is_zero_and_an_empty_list(monkeypatch):
    result = await _upload({}, monkeypatch=monkeypatch)

    assert result["fields_skipped"] == 0
    assert result["skipped_fields"] == []


async def test_empty_fields_are_not_skipped_fields(monkeypatch):
    # A /generate record carries every field of the schema, most of them empty.
    result = await _upload(
        {"schema:startDate": None, "schema:endDate": "", "oeh:eventType": []},
        monkeypatch=monkeypatch,
    )

    assert result["fields_skipped"] == 0


async def test_a_field_written_under_another_name_is_not_skipped(monkeypatch):
    result = await _upload(
        {
            "cm:author": ["Erika Mustermann"],
            "ccm:custom_license": "https://creativecommons.org/licenses/by-sa/4.0/",
            "oeh:new_lrt": ["http://w3id.org/openeduhub/vocabs/new_lrt/36e68792"],
            "schema:location": [{"geo": {"latitude": 52.5, "longitude": 13.4}}],
            "virtual:collection_id_primary": COLLECTION,
            "ccm:collection_id": [COLLECTION],
        },
        monkeypatch=monkeypatch,
    )

    assert result["skipped_fields"] == []


async def test_a_location_without_coordinates_is_skipped(monkeypatch):
    result = await _upload(
        {"schema:location": [{"name": "Aula"}]}, monkeypatch=monkeypatch
    )

    assert result["skipped_fields"] == ["schema:location"]


class _BulkRefused(FakeRepository):
    """Refuses the bulk write and the single write of the description."""

    async def answer(self, method, url, body):
        if "METADATA_UPDATE" in url and (
            len(body) > 1 or "cclom:general_description" in body
        ):
            return Response(400, {"message": "nein"})
        return await super().answer(method, url, body)


async def test_in_the_field_by_field_fallback_refused_fields_count_as_well(
    monkeypatch,
):
    result = await _upload(
        {"cclom:general_description": "Text", "ccm:erfundenes_feld": "wert"},
        _BulkRefused,
        monkeypatch,
    )

    assert result["fields_written"] == 2
    # One the filter dropped, one the repository refused (named in field_errors).
    assert result["fields_skipped"] == 2
    assert result["skipped_fields"] == ["ccm:erfundenes_feld"]
    assert [e["field_id"] for e in result["field_errors"]] == [
        "cclom:general_description"
    ]


def test_the_typed_result_carries_the_names():
    result = WriteResult.from_service(
        {"success": True, "fields_skipped": 1, "skipped_fields": ["ccm:erfunden"]}
    )

    assert result.skipped_fields == ["ccm:erfunden"]
