"""
What a request names has a length, and an answer or log line repeats at most
200 characters of it (audit 14.11., S-34).

A `context` of 1 MB came back as a 404 of 2 MB, with a WARNING of 2 MB and an
INFO line of 1 MB. Bounded now: context 64, version 32, schema_file 300,
field_id 200, language 8, each collection and workflow state 200 - as options,
as markers inside the record and as path parameters.
"""

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src import main
from src.api import deps
from src.api.responses import failure_response
from src.models.schemas import (
    DetectContentTypeRequest,
    ExportMarkdownRequest,
    ExtractFieldRequest,
    GenerateRequest,
    SaveNodeRequest,
    UploadRequest,
    ValidateRequest,
    WorkflowRequest,
)
from src.utils import schema_loader
from src.utils.loggable import shortened

RECORD = {"metadata": {"cclom:title": "T"}}
FIELD = {"schema_file": "core.json", "field_id": "cclom:title"}
LIMITS = {
    "context": 64,
    "version": 32,
    "schema_file": 300,
    "field_id": 200,
    "language": 8,
}

OPTIONS = [
    (GenerateRequest, {"text": "x"}, ("context", "version", "schema_file", "language")),
    (DetectContentTypeRequest, {"text": "x"}, ("context", "version", "language")),
    (
        ExtractFieldRequest,
        {"text": "x", **FIELD},
        ("context", "version", "schema_file", "field_id", "language"),
    ),
    (ValidateRequest, RECORD, ("context", "version", "schema_file")),
    (ExportMarkdownRequest, RECORD, ("context", "version", "schema_file", "language")),
]


@pytest.mark.parametrize(
    "model, body, name",
    [(model, body, name) for model, body, names in OPTIONS for name in names],
)
def test_an_option_has_its_length(model, body, name):
    limit = LIMITS[name]

    assert model(**{**body, name: "x" * limit})
    with pytest.raises(ValidationError, match=f"at most {limit}"):
        model(**{**body, name: "x" * (limit + 1)})


MARKERS = {"contextName": 64, "schemaVersion": 32, "metadataset": 300}


@pytest.mark.parametrize(
    "model, key",
    [
        (ValidateRequest, "metadata"),
        (ExportMarkdownRequest, "metadata"),
        (UploadRequest, "metadata"),
        (SaveNodeRequest, "metadata"),
    ],
)
@pytest.mark.parametrize("marker", sorted(MARKERS))
def test_a_marker_inside_the_record_has_the_same_length(model, key, marker):
    limit = MARKERS[marker]

    assert model(**{key: {"cclom:title": "T", marker: "x" * limit}})
    with pytest.raises(ValidationError, match=f"{marker} darf höchstens {limit}"):
        model(**{key: {"cclom:title": "T", marker: "x" * (limit + 1)}})


def test_the_legacy_markers_and_the_export_language_too():
    with pytest.raises(ValidationError, match="_schema.context darf höchstens 64"):
        ValidateRequest(metadata={"_schema": {"context": "x" * 65}})
    with pytest.raises(ValidationError, match="language darf höchstens 8"):
        ExportMarkdownRequest(metadata={"language": "x" * 9})


NODE = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"


@pytest.mark.parametrize(
    "model, body",
    [
        (UploadRequest, {**RECORD, "collection_id": [NODE, "c" * 201]}),
        (SaveNodeRequest, {**RECORD, "collection_id": [NODE, "c" * 201]}),
        (UploadRequest, {**RECORD, "workflow_steps": ["200_tocheck", "s" * 201]}),
        (WorkflowRequest, {"steps": ["200_tocheck", "s" * 201]}),
    ],
)
def test_an_entry_of_a_list_has_its_length(model, body):
    with pytest.raises(ValidationError) as refused:
        model(**body)

    assert "c" * 201 not in str(refused.value)
    assert "s" * 201 not in str(refused.value)


def test_many_unknown_workflow_states_give_a_short_message():
    with pytest.raises(ValidationError) as refused:
        WorkflowRequest(steps=[f"unbekannt_{i}" for i in range(10_000)])

    assert len(refused.value.errors()[0]["msg"]) < 1_000


# ------------------------------------------------------------ path parameters


@pytest.mark.parametrize(
    "path",
    [
        f"/info/schemas/{'c' * 65}/latest",
        f"/info/schemas/default/{'9' * 33}",
        f"/info/schema/{'c' * 65}/latest/core.json",
        f"/info/schema/default/{'9' * 33}/core.json",
        f"/info/schema/default/latest/{'s' * 301}",
        f"/widget/i18n/{'l' * 9}.json",
    ],
)
def test_a_path_parameter_has_its_length(path):
    with TestClient(main.app) as client:
        response = client.get(path)

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "string_too_long"
    assert len(response.content) < 1_000


# ------------------------------------------------------------ quoted values


def test_shortened_keeps_200_characters():
    assert shortened("kurz") == "kurz"
    assert shortened("x" * 201) == "x" * 200 + "…"
    assert shortened(5) == "5"


LONG = "L" * 5_000


@pytest.mark.parametrize(
    "call",
    [
        lambda: schema_loader.load_schema(LONG, "2.0.0", "core.json"),
        lambda: schema_loader.load_schema("default", LONG, "core.json"),
        lambda: schema_loader.load_schema("default", "2.0.0", LONG),
        lambda: schema_loader.load_schema("default", "2.0.0", "nope.json"),
        lambda: schema_loader.load_manifest(LONG),
        lambda: schema_loader.get_available_schemas("default", LONG),
        lambda: schema_loader.resolve_schema_file_or_uri(
            "https://" + LONG, "default", "2.0.0"
        ),
    ],
)
def test_a_schema_error_quotes_at_most_200_characters_of_a_value(call):
    with pytest.raises(ValueError) as refused:
        call()

    assert len(str(refused.value)) < 400


class _Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.longest = 0

    def emit(self, record):
        self.longest = max(self.longest, len(record.getMessage()))


@pytest.fixture
def lines():
    handler = _Lines()
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield handler
    root.removeHandler(handler)
    root.setLevel(level)


@pytest.mark.parametrize(
    "path, body",
    [
        ("/generate", {"text": "abc", "context": "C" * 1_000_000}),
        ("/extract-field", {"text": "abc", **FIELD, "field_id": "F" * 1_000_000}),
        ("/validate", {**RECORD, "schema_file": "S" * 1_000_000}),
        ("/validate", {"metadata": {"contextName": "C" * 1_000_000}}),
        ("/export/markdown", {"metadata": {"metadataset": "S" * 1_000_000}}),
        ("/detect-content-type", {"text": "abc", "version": "V" * 1_000_000}),
    ],
)
def test_a_megabyte_in_a_name_gives_a_short_answer_and_short_log_lines(
    monkeypatch, lines, path, body
):
    monkeypatch.setattr(
        deps, "get_llm_service", lambda **kw: pytest.fail("LLM asked for")
    )
    with TestClient(main.app) as client:
        response = client.post(path, json=body)

    assert response.status_code == 422, response.text[:300]
    assert len(response.content) < 2_000
    assert lines.longest < 3_000


def test_a_log_line_of_an_error_answer_has_a_length(lines):
    failure_response(502, "D" * 100_000, {"code": "X", "message": "M" * 100_000})

    assert 0 < lines.longest < 3_000
