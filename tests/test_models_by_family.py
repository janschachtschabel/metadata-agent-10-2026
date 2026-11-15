"""
The request and response models live in one module per family; `models.schemas`
re-exports all of them, and the schema markers have one validator (audit
14.11.2026, Q-07).

Before, schemas.py held every model in 1,777 lines and the validator that
checks `contextName`, `schemaVersion` and `metadataset` stood in it five times.
"""

import ast
import importlib
import inspect

import pytest
from pydantic import BaseModel, ValidationError

from src.models import schemas
from src.models.common import SchemaMarkersMixin

FAMILIES = (
    "common",
    "extraction",
    "validation",
    "schema_info",
    "screenshot",
    "repository",
)
WITH_MARKERS = {
    schemas.ValidateRequest: "metadata",
    schemas.ExportMarkdownRequest: "metadata",
    schemas.UploadRequest: "metadata",
    schemas.SaveNodeRequest: "metadata",
    schemas.VerifyRequest: "expected_metadata",
}


def _defined_in(module) -> set[str]:
    """Public names the module defines itself (classes, functions, constants)."""
    names = set()
    for node in ast.parse(inspect.getsource(module)).body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets)
    return {name for name in names if not name.startswith("_")}


@pytest.mark.parametrize("family", FAMILIES)
def test_the_facade_exports_every_public_name_of_a_family(family):
    module = importlib.import_module(f"src.models.{family}")
    names = _defined_in(module)

    assert names, family
    assert names <= set(schemas.__all__)
    assert all(getattr(schemas, name) is getattr(module, name) for name in names)


def test_the_facade_defines_nothing_itself():
    own = {
        name
        for name, obj in vars(schemas).items()
        if getattr(obj, "__module__", None) == schemas.__name__
    }

    assert own == set()


def test_no_family_module_imports_the_facade():
    for family in FAMILIES:
        source = inspect.getsource(importlib.import_module(f"src.models.{family}"))
        assert "schemas import" not in source, family


def test_every_model_with_a_metadata_record_uses_the_shared_validator():
    with_record = {
        obj
        for obj in vars(schemas).values()
        if inspect.isclass(obj)
        and issubclass(obj, BaseModel)
        and {"metadata", "expected_metadata"} & set(obj.model_fields)
        and obj.__name__.endswith("Request")
        and obj is not schemas.CreateNodeRequest
    }

    assert with_record == set(WITH_MARKERS)
    for model in WITH_MARKERS:
        assert issubclass(model, SchemaMarkersMixin)
        assert "_markers_are_text" not in vars(model)


@pytest.mark.parametrize("model, field", WITH_MARKERS.items())
@pytest.mark.parametrize(
    "record, message",
    [
        ({"contextName": 5}, "contextName muss ein Text sein"),
        ({"schemaVersion": "1" * 33}, "schemaVersion darf höchstens 32 Zeichen"),
        ({"metadataset": []}, "metadataset muss ein Text sein"),
        ({"_schema": "x"}, "_schema muss ein Objekt sein"),
        ({"_schema": {"file": 1}}, "_schema.file muss ein Text sein"),
    ],
)
def test_a_marker_of_the_wrong_kind_is_refused_on_every_model(
    model, field, record, message
):
    with pytest.raises(ValidationError) as refused:
        model(**{field: {"cclom:title": "x", **record}})

    assert refused.value.errors()[0]["loc"] == (field,)
    assert message in refused.value.errors()[0]["msg"]


@pytest.mark.parametrize("model, field", WITH_MARKERS.items())
def test_language_is_a_marker_of_the_markdown_export_only(model, field):
    record = {"cclom:title": "x", "language": 5}

    if model is schemas.ExportMarkdownRequest:
        with pytest.raises(ValidationError, match="language muss ein Text sein"):
            model(**{field: record})
    else:
        assert getattr(model(**{field: record}), field) == record


def test_verify_without_a_record_stays_valid():
    assert schemas.VerifyRequest().expected_metadata is None
    assert schemas.VerifyRequest(expected_metadata=None).expected_metadata is None


def test_the_mixin_adds_nothing_to_a_model_schema():
    schema = schemas.ValidateRequest.model_json_schema()

    assert list(schema["properties"]) == [
        "metadata",
        "context",
        "version",
        "schema_file",
    ]
    assert "schema_markers" not in str(schema)
    assert SchemaMarkersMixin.model_fields == {}
