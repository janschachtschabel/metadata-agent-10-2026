"""
/validate findings of the final pre-release audit (2026-10-18).

P-07: the "Did you mean …?" search compared every invalid value with every label
of every language under a generous limit - 200 invalid occupational categories
(894 concepts) blocked the event loop for 12.6 s. Coordinates: any field id
containing "lat" (maximumPhysicalAttendeeCapacity, platform, relatedLink) was
range-checked as a latitude and turned a capacity of 200 into an error.
"""

import pytest

from src.services import metadata_validation
from src.services.metadata_service import get_metadata_service

FIELD = "schema:occupationalCategory"


def _validate(metadata, schema_file):
    return get_metadata_service().validate_metadata(
        metadata, context="default", version="2.0.0", schema_file=schema_file
    )


@pytest.fixture
def counted_levenshtein(monkeypatch):
    calls = {"count": 0, "limits": set()}
    original = metadata_validation.levenshtein_within

    def counting(a, b, limit):
        calls["count"] += 1
        calls["limits"].add(limit)
        return original(a, b, limit)

    monkeypatch.setattr(metadata_validation, "levenshtein_within", counting)
    return calls


def test_many_invalid_values_get_suggestions_for_only_a_few(counted_levenshtein):
    values = [f"Unbekannter Beruf Nummer {i}" for i in range(200)]

    result = _validate({FIELD: values}, "education_offer.json")

    warnings = [w for w in result["warnings"] if w["field_id"] == FIELD]
    assert len(warnings) == 200  # every invalid value is still reported
    searched = metadata_validation.MAX_SUGGESTIONS_PER_FIELD
    labels_per_search = 894 * 2  # at most de + en per concept
    assert counted_levenshtein["count"] <= searched * labels_per_search


def test_the_suggestion_limit_matches_the_other_fuzzy_paths(counted_levenshtein):
    metadata_validation.find_closest_vocabulary_match(
        "a very long value with many characters", [{"uri": "u", "label": {"de": "x"}}]
    )
    metadata_validation.find_closest_vocabulary_match(
        "ab", [{"uri": "u", "label": {"de": "x"}}]
    )
    assert counted_levenshtein["limits"] == {3, 1}


def test_a_value_four_edits_away_gets_no_suggestion():
    concepts = [{"uri": "u/1", "label": {"de": "Mathematikunterricht"}}]
    assert (
        metadata_validation.find_closest_vocabulary_match(
            "Mathematikunterrichtxxxx", concepts
        )
        is None
    )


def test_a_close_value_still_gets_its_suggestion():
    concepts = [{"uri": "u/1", "label": {"de": "Mathematik"}}]
    assert (
        metadata_validation.find_closest_vocabulary_match("Mathemtik", concepts)
        == "u/1"
    )


@pytest.mark.parametrize(
    "field_id",
    [
        "schema:maximumPhysicalAttendeeCapacity",
        "schema:maximumVirtualAttendeeCapacity",
    ],
)
def test_a_capacity_is_no_latitude(field_id):
    result = _validate({field_id: 200}, "event.json")

    assert not [e for e in result["errors"] if e["field_id"] == field_id]


@pytest.mark.parametrize(
    "field_id, value",
    [
        ("schema:latitude", 91),
        ("latitude", -91),
        ("geo.lat", 100),
        ("schema:longitude", 181),
        ("lng", -200),
        ("schema:lon", 190),
    ],
)
def test_a_real_coordinate_out_of_range_is_still_an_error(field_id, value):
    errors = metadata_validation._coordinate_errors(field_id, [value])
    assert len(errors) == 1 and "out of range" in errors[0]["message"]


@pytest.mark.parametrize(
    "field_id", ["ccm:platform", "prompt:relatedPrompts", "sdo:relatedLink"]
)
def test_ids_merely_containing_lat_or_lon_are_not_coordinates(field_id):
    assert metadata_validation._coordinate_errors(field_id, [500]) == []


# ------------------------------------- API-07 / API-10: marker types, 422 form


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from src import main

    return TestClient(main.app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/validate", "/export/markdown"])
@pytest.mark.parametrize(
    "markers",
    [
        {"schemaVersion": 2},
        {"metadataset": {"datei": "event.json"}},
        {"contextName": ["default"]},
        {"_schema": "default"},
        {"_schema": {"version": 2}},
    ],
)
def test_a_marker_that_is_no_text_is_a_422(client, path, markers):
    response = client.post(path, json={"cclom:title": "Titel", **markers})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail.startswith("Ungültiger Request-Body: metadata:")
    assert "unhashable" not in detail and "pydantic" not in detail


def test_export_refuses_a_language_that_is_no_text(client):
    response = client.post("/export/markdown", json={"cclom:title": "T", "language": 5})

    assert response.status_code == 422, response.text


@pytest.mark.parametrize("path", ["/validate", "/export/markdown"])
def test_a_refused_parameter_has_the_shared_422_form(client, path):
    response = client.post(
        path, json={"metadata": {"cclom:title": "T"}, "version": ["geheim-1"]}
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail.startswith("Ungültiger Request-Body: version:")
    assert "geheim-1" not in detail and "pydantic" not in detail


@pytest.mark.parametrize("path", ["/validate", "/export/markdown"])
def test_text_markers_still_work(client, path):
    response = client.post(
        path,
        json={
            "contextName": "default",
            "schemaVersion": "2.0.0",
            "metadataset": "event.json",
            "language": None,
            "cclom:title": "Titel",
        },
    )

    assert response.status_code == 200, response.text
