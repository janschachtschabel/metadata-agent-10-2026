"""
The answer of /validate is bounded, whatever the record holds (audit 14.11., S-30).

Every value of a list got its own issue, each quoting the value in full:
1,000,000 one-letter values (4 MB) came back as 106 MB and took 1 GB of memory
on the way. Now a check lists at most MAX_ISSUES_PER_FIELD problems of a field
and says how many values it left out, a quoted value is cut, an answer holds at
most MAX_ISSUES errors and as many warnings, and /validate and /export/markdown
take a body of at most 2 MiB.
"""

import json

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api.routers import validation as validation_router
from src.services import metadata_validation
from src.services.metadata_service import get_metadata_service

# Closed vocabulary (core.json) and object field (event.json) of schema 2.0.0.
VOCABULARY_FIELD = "ccm:educationalcontext"
OBJECT_FIELD = "schema:location"
PER_FIELD = metadata_validation.MAX_ISSUES_PER_FIELD


def _validate(metadata, schema_file="event.json"):
    return get_metadata_service().validate_metadata(
        metadata, context="default", version="2.0.0", schema_file=schema_file
    )


def _of(issues, field_id):
    return [issue for issue in issues if issue["field_id"] == field_id]


def test_a_million_values_give_a_small_answer():
    result = _validate({VOCABULARY_FIELD: ["a"] * 1_000_000})

    assert len(json.dumps(result)) < 100_000
    warnings = _of(result["warnings"], VOCABULARY_FIELD)
    assert len(warnings) == PER_FIELD + 1
    assert "999980" in warnings[-1]["message"]


def test_the_closing_entry_counts_the_values_left_out():
    result = _validate({OBJECT_FIELD: [f"Ort {i}" for i in range(25)]})

    errors = _of(result["errors"], OBJECT_FIELD)
    assert len(errors) == PER_FIELD + 1
    assert "Ort 19" in errors[PER_FIELD - 1]["message"]
    assert "Ort 20" not in json.dumps(errors)
    assert "5" in errors[-1]["message"]
    assert result["valid"] is False


def test_as_many_problems_as_the_bound_need_no_closing_entry():
    result = _validate({OBJECT_FIELD: [f"Ort {i}" for i in range(PER_FIELD)]})

    assert len(_of(result["errors"], OBJECT_FIELD)) == PER_FIELD


@pytest.mark.parametrize(
    "field_id, value",
    [
        (OBJECT_FIELD, "o" * 5_000),
        (VOCABULARY_FIELD, "v" * 5_000),
        ("ccm:oeh_event_begin", "d" * 5_000),
        ("ccm:wwwurl", "u" * 5_000),
    ],
)
def test_a_quoted_value_is_cut(field_id, value):
    result = _validate({field_id: [value]})

    issues = _of(result["errors"] + result["warnings"], field_id)
    assert issues
    longest = metadata_validation.MAX_QUOTED_VALUE_LENGTH
    for issue in issues:
        # The object error quotes the value twice.
        assert len(issue["message"]) < 2 * longest + 200


def test_an_answer_lists_no_more_than_the_overall_bound(monkeypatch):
    monkeypatch.setattr(metadata_validation, "MAX_ISSUES", 30)
    metadata = {
        "cclom:title": "Titel",
        OBJECT_FIELD: ["x"] * 20,
        "schema:organizer": ["x"] * 20,
        VOCABULARY_FIELD: ["x"] * 20,
        "ccm:taxonid": ["x"] * 20,
    }

    result = _validate(metadata)

    for issues in (result["errors"], result["warnings"]):
        assert len(issues) == 31
        assert issues[-1]["field_id"] == "_limit"
        assert "10" in issues[-1]["message"]


# ------------------------------------------------------------ body limit


@pytest.mark.parametrize("path", ["/validate", "/export/markdown"])
def test_a_body_over_two_mebibytes_is_refused(path):
    body = json.dumps({"metadata": {"cclom:title": "t" * (2 * 1024 * 1024)}})

    with TestClient(main.app) as client:
        response = client.post(
            path, content=body, headers={"content-type": "application/json"}
        )

    assert response.status_code == 413
    answer = response.json()
    assert answer["error"]["code"] == "REQUEST_TOO_LARGE"
    assert str(validation_router.MAX_BODY_BYTES) in answer["detail"]


def test_the_largest_accepted_body_gives_a_small_answer():
    values = ["a"] * 500_000  # 4 bytes each: just under 2 MiB
    body = json.dumps(
        {"metadata": {VOCABULARY_FIELD: values, "metadataset": "event.json"}},
        separators=(",", ":"),
    )
    assert len(body) < validation_router.MAX_BODY_BYTES

    with TestClient(main.app) as client:
        response = client.post(
            "/validate", content=body, headers={"content-type": "application/json"}
        )

    assert response.status_code == 200
    assert len(response.content) < 100_000
