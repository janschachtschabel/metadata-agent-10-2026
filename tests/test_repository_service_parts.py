"""
RepositoryService keeps the write flow; the rest lives in modules of its own
(audit 14.11.2026, Q-07).

The write model (repository_write), the editing window, the preview upload and
the unfinished write (three mixins, like nodes, metadata and review) and the
result dicts (functions in repository_result) left repository_service.py,
which had 1,411 lines. Nothing they do changed; every name the module defined
is still importable from it.
"""

import ast
import inspect
from pathlib import Path

import pytest

from src.services import repository_result as result_module
from src.services import repository_service as repo_module
from src.services.repository_cut_off import CutOffMixin
from src.services.repository_edit_window import NODE_EDIT_WINDOW, EditWindowMixin
from src.services.repository_preview_upload import PreviewUploadMixin
from src.services.repository_service import RepositoryService
from src.services.repository_write import (
    NodeWrite,
    PreviewSource,
    StepResults,
    WriteProgress,
    WriteTarget,
    with_resolved_schema,
)

SERVICES = Path(repo_module.__file__).parent
BASE = "https://repository.example/edu-sharing"

# method -> the part of the service that holds it
PARTS = {
    "create_node": RepositoryService,
    "upload_metadata": RepositoryService,
    "save_node": RepositoryService,
    "write_node": RepositoryService,
    "_write_steps": RepositoryService,
    "_after_metadata": RepositoryService,
    "_create_tracked": RepositoryService,
    "_reject_if_too_old": EditWindowMixin,
    "_edit_check": EditWindowMixin,
    "preview_refusal": PreviewUploadMixin,
    "upload_preview": PreviewUploadMixin,
    "_bounded_preview": PreviewUploadMixin,
    "_preview_from_source": PreviewUploadMixin,
    "_fetched_preview": PreviewUploadMixin,
    "_post_preview": PreviewUploadMixin,
    "_create_cut_off": CutOffMixin,
    "_clean_up_cut_off": CutOffMixin,
    "_cut_off_result": CutOffMixin,
    "_write_failed": CutOffMixin,
    "_metadata_rejected": CutOffMixin,
    "_failed": CutOffMixin,
}


@pytest.mark.parametrize("method, part", PARTS.items())
def test_a_method_lives_in_its_part_and_the_service_has_it(method, part):
    assert method in vars(part)
    assert getattr(RepositoryService, method) is vars(part)[method]


def test_no_part_defines_a_method_of_another():
    parts = [base for base in RepositoryService.__mro__ if base is not object]
    seen: dict[str, str] = {}
    for part in parts:
        for name, member in vars(part).items():
            if inspect.isfunction(member):
                assert name not in seen, f"{name}: {seen.get(name)} and {part.__name__}"
                seen[name] = part.__name__


@pytest.mark.parametrize(
    "name, home",
    [
        ("NodeWrite", NodeWrite),
        ("with_resolved_schema", with_resolved_schema),
        ("PreviewSource", PreviewSource),
        ("NODE_EDIT_WINDOW", NODE_EDIT_WINDOW),
    ],
)
def test_a_moved_name_is_still_importable_from_the_service_module(name, home):
    assert getattr(repo_module, name) is home


@pytest.mark.parametrize(
    "module, limit",
    [
        ("repository_service.py", 740),
        ("repository_write.py", 200),
        ("repository_result.py", 300),
        ("repository_edit_window.py", 200),
        ("repository_preview_upload.py", 300),
        ("repository_cut_off.py", 350),
    ],
)
def test_no_part_grows_back_into_one_big_module(module, limit):
    lines = (SERVICES / module).read_text(encoding="utf-8").count("\n")

    assert lines <= limit, f"{module}: {lines} lines"


def test_the_result_builders_are_functions_without_a_service():
    for name in ("created_result", "duplicate_result", "written_result"):
        builder = getattr(result_module, name)
        assert inspect.isfunction(builder)
        assert "self" not in inspect.signature(builder).parameters
        assert not inspect.iscoroutinefunction(builder)


def test_the_result_module_does_not_import_the_service():
    tree = ast.parse((SERVICES / "repository_result.py").read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "repository_service" not in imported
    assert "repository_write" in imported


def test_created_result_names_the_node_as_before():
    record = {"cclom:title": ["Titel"], "ccm:wwwurl": "https://example.org/seite"}

    assert result_module.created_result(BASE, "node-1", record) == {
        "success": True,
        "node": {
            "nodeId": "node-1",
            "title": "Titel",
            "wwwurl": "https://example.org/seite",
            "repositoryUrl": f"{BASE}/components/render/node-1",
        },
    }


@pytest.mark.parametrize(
    "duplicate, error, has_node",
    [
        ({"nodeId": "n", "title": "Alt"}, 'URL existiert bereits: "Alt"', True),
        ({"nodeId": "n"}, "URL existiert bereits: Node n", True),
        ({}, "URL existiert bereits", False),
    ],
)
def test_duplicate_result_names_the_existing_node(duplicate, error, has_node):
    record = {"ccm:wwwurl": "https://example.org/seite"}

    result = result_module.duplicate_result(BASE, duplicate, record)

    assert (result["success"], result["duplicate"]) == (False, True)
    assert result["error"] == error
    assert ("node" in result) is has_node
    assert "node_full" not in result
    if has_node:
        assert result["node"]["repositoryUrl"] == f"{BASE}/components/render/n"
        assert result["node"]["wwwurl"] == "https://example.org/seite"


def test_written_result_reports_what_the_steps_answered():
    target = WriteTarget(
        {"cclom:title": "Titel", "cclom:general_description": "x" * 250},
        "event.json",
        {"cclom:title", "cclom:general_description"},
        "inbox",
    )
    progress = WriteProgress(created_node_id="node-1")
    steps = StepResults(
        collections={"results": [{"collectionId": "c", "success": True}]},
        workflow={"steps": [{"status": "200_tocheck", "success": True}]},
        preview={"success": True},
    )
    written = {"fields_written": 2, "fields_skipped": 1, "skipped_fields": ["a"]}

    result = result_module.written_result(
        BASE, "node-1", target, progress, written, steps
    )

    assert result["success"] is True
    assert result["schema_used"] == "event.json"
    assert result["repo_fields_available"] == 2
    assert result["node_created"] is True
    assert result["node"]["description"] == "x" * 200 + "..."
    assert (result["fields_written"], result["fields_skipped"]) == (2, 1)
    assert result["skipped_fields"] == ["a"]
    assert result["collections"] == [{"collectionId": "c", "success": True}]
    assert result["workflow"] == [{"status": "200_tocheck", "success": True}]
    assert result["preview"] == {"success": True}
    assert "field_errors" not in result


def test_a_handed_over_node_is_not_reported_as_created():
    target = WriteTarget({"cclom:title": "Titel"}, None, {"cclom:title"}, "inbox")

    result = result_module.written_result(
        BASE, "node-1", target, WriteProgress(), {}, StepResults()
    )

    assert result["node_created"] is False
    assert {"collections", "workflow", "preview"}.isdisjoint(result)
