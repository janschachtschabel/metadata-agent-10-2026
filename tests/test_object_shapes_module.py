"""
The object-shape check is a module of functions; the repository write path no
longer imports the normalizer of LLM answers for it (audit 14.11.2026, Q-07).

`find_object_shape_violations` stood in field_normalizer.py and went through a
FieldNormalizer instance to ask two questions about a schema. Both are
functions of services/object_shapes.py now; FieldNormalizer asks them too.
"""

import ast
from pathlib import Path

import pytest

from src.services import field_normalizer, object_shapes
from src.services.field_normalizer import FieldNormalizer

SERVICES = Path(object_shapes.__file__).parent
WRITE_PATH = sorted(SERVICES.glob("repository_*.py"))

OBJECT_SCHEMAS = [
    {"datatype": "object"},
    {"datatype": "array", "items": {"datatype": "object"}},
    {"datatype": "array", "items": {"variants": [{"fields": [{"id": "name"}]}]}},
    {"datatype": "string", "variants": [{"fields": [{"id": "contactType"}]}]},
    {"datatype": "object", "multiple": True, "items": None},
]
OTHER_SCHEMAS = [
    {},
    {"datatype": "string"},
    {"datatype": "array", "items": {"datatype": "string"}},
    {"datatype": "array", "items": {"variants": []}},
]


def _imported_modules(path: Path) -> set[str]:
    return {
        node.module
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    }


def test_the_write_path_does_not_import_the_field_normalizer():
    assert len(WRITE_PATH) > 10
    users = [p.name for p in WRITE_PATH if "field_normalizer" in _imported_modules(p)]

    assert users == []
    assert "object_shapes" in _imported_modules(SERVICES / "repository_service.py")
    assert "object_shapes" in _imported_modules(SERVICES / "repository_metadata.py")


def test_the_shape_module_needs_neither_normalizer_nor_vocabularies():
    imported = _imported_modules(SERVICES / "object_shapes.py")

    assert imported == {"typing", "utils.schema_loader"}


def test_the_old_import_path_still_gives_the_same_function():
    assert (
        field_normalizer.find_object_shape_violations
        is object_shapes.find_object_shape_violations
    )


@pytest.mark.parametrize("schema", OBJECT_SCHEMAS)
def test_an_object_field_is_recognised_by_function_and_normalizer(schema):
    assert object_shapes.is_object_field(schema) is True
    assert FieldNormalizer()._is_object_field(schema) is True


@pytest.mark.parametrize("schema", OTHER_SCHEMAS)
def test_other_fields_are_not_object_fields(schema):
    assert object_shapes.is_object_field(schema) is False
    assert FieldNormalizer()._is_object_field(schema) is False


@pytest.mark.parametrize(
    "schema, key",
    [
        ({"datatype": "object"}, "name"),
        (
            {"items": {"variants": [{"fields": [{"id": "url"}, {"id": "name"}]}]}},
            "name",
        ),
        ({"items": {"variants": [{"fields": [{"id": "contactType"}]}]}}, "contactType"),
        ({"variants": [{"fields": []}, {"fields": [{"id": "email"}]}]}, "email"),
    ],
)
def test_the_key_of_a_bare_string_is_the_same_for_both(schema, key):
    assert object_shapes.object_key(schema) == key
    assert FieldNormalizer()._object_key(schema) == key


def test_a_violation_names_field_value_and_expected_key():
    metadata = {
        "cclom:title": "Deutsches Technikmuseum",
        "schema:contactPoint": "Besucherservice",
    }

    violations = object_shapes.find_object_shape_violations(
        metadata, "mds_oeh", "2.0.0", "organization.json"
    )

    assert [v["field_id"] for v in violations] == ["schema:contactPoint"]
    assert violations[0]["value"] == "Besucherservice"
    assert violations[0]["expected_key"] == "contactType"
    assert '{"contactType": "..."}' in violations[0]["message"]
