"""
No function sits at the complexity ceiling, and the two longest parameter
lists are objects their callers build (audit 14.11.2026, Q-10).

Twenty functions stood between complexity 11 and the ceiling of 15, three of
them at 15; each was split along what it already did in sequence. The ceiling
in pyproject.toml stays 15 - this test keeps the distance to it.
`upload_metadata` took 16 arguments and `generate_metadata` 15: the routers
now describe a write as NodeWrite and a run as GenerationOptions.
"""

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from src.api.routers import generate as generate_router
from src.api.routers import save_node as save_node_router
from src.api.routers import upload as upload_router
from src.models.repository_rules import DEFAULT_WORKFLOW_STATUS
from src.services.metadata_generation import GenerationOptions
from src.services.metadata_service import MetadataService
from src.services.repository_preview_upload import fetched_preview
from src.services.repository_service import RepositoryService
from src.services.repository_write import NodeWrite, upload_workflow_steps

ROOT = Path(__file__).resolve().parents[1]
HEADROOM = 12


def test_no_function_of_src_is_more_complex_than_twelve():
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "src",
            "--select",
            "C901",
            "--config",
            f"lint.mccabe.max-complexity={HEADROOM}",
            "--no-cache",
            "--output-format",
            "concise",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert run.returncode == 0, run.stdout + run.stderr


def _parameters(function) -> list[str]:
    return list(inspect.signature(function).parameters)


def test_the_write_path_takes_the_write_and_its_deadline():
    assert _parameters(RepositoryService.write_node) == ["self", "job", "deadline"]


@pytest.mark.parametrize("router", [upload_router, save_node_router])
def test_the_write_routes_build_the_node_write_themselves(router):
    source = inspect.getsource(router)

    assert "NodeWrite(" in source
    assert "repo_service.write_node(" in source
    assert "repo_service.upload_metadata(" not in source
    assert "repo_service.save_node(" not in source


@pytest.mark.parametrize(
    "start, named, steps",
    [
        (True, None, [DEFAULT_WORKFLOW_STATUS]),
        (True, [], [DEFAULT_WORKFLOW_STATUS]),
        (True, ["140_ELEMENT_LEGALLY_APPROVED"], ["140_ELEMENT_LEGALLY_APPROVED"]),
        (False, None, []),
        (False, ["140_ELEMENT_LEGALLY_APPROVED"], []),
    ],
)
def test_the_states_of_an_upload(start, named, steps):
    assert upload_workflow_steps(start, named) == steps


async def test_the_keyword_forms_describe_the_same_write(monkeypatch):
    written = []

    async def write_node(self, job, deadline=None):
        written.append((job, deadline))
        return {"success": True}

    monkeypatch.setattr(RepositoryService, "write_node", write_node)
    service = RepositoryService("u", "p")
    record = {"cclom:title": "Titel"}

    await service.upload_metadata(
        record, return_full_node=True, collection_ids=["c"], deadline=5.0
    )
    await service.upload_metadata(record, start_workflow=False, node_id="")
    await service.save_node(record, node_id="n", workflow_steps=["200_tocheck"])

    assert written[0] == (
        NodeWrite(
            metadata=record,
            check_duplicates=True,
            read_back=True,
            collection_ids=["c"],
            workflow_steps=[DEFAULT_WORKFLOW_STATUS],
        ),
        5.0,
    )
    assert written[1][0] == NodeWrite(
        metadata=record, check_duplicates=True, workflow_steps=[]
    )
    assert written[2][0] == NodeWrite(
        metadata=record, node_id="n", read_back=True, workflow_steps=["200_tocheck"]
    )


def test_the_preview_source_of_a_write_needs_no_service():
    assert inspect.iscoroutinefunction(fetched_preview("https://example.org/a.png"))
    assert inspect.iscoroutinefunction(
        RepositoryService("u", "p")._fetched_preview("https://example.org/a.png")
    )


def test_a_run_is_described_by_its_options():
    assert _parameters(MetadataService.generate_metadata) == [
        "self",
        "text",
        "options",
        "keywords",
    ]
    assert "GenerationOptions(" in inspect.getsource(generate_router._run)


def test_the_options_default_to_what_the_keywords_defaulted_to():
    assert GenerationOptions() == GenerationOptions(
        context="default",
        version="latest",
        schema_file="auto",
        existing_metadata=None,
        language="de",
        max_workers=20,
        include_core=True,
        enable_geocoding=True,
        normalize_output=True,
        normalize_vocabularies=True,
        regenerate_fields=None,
        regenerate_empty=False,
        origins=None,
        deadline=None,
    )


async def test_options_and_keywords_together_are_refused():
    service = MetadataService.__new__(MetadataService)

    with pytest.raises(TypeError, match="options or keywords"):
        await service.generate_metadata("Text", GenerationOptions(), language="en")
    with pytest.raises(TypeError, match="unbekannt"):
        await service.generate_metadata("Text", unbekannt=True)
