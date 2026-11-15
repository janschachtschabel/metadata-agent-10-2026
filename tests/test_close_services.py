"""
The service layer closes what it keeps open (audit 14.11.2026, Q-18).

At shutdown src/main.py imported the private singletons of three service
modules (`_llm_service`, `_metadata_service`, `_input_source_service`) and
closed their HTTP clients itself. The services now offer close_services(), and
the app calls that.
"""

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from src import main
from src.services import (
    input_source_service,
    lifecycle,
    llm_service,
    metadata_service,
)

SRC = Path(__file__).resolve().parents[1] / "src"


class _Closable:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


class _Metadata:
    def __init__(self, llm):
        self.llm_service = llm


async def test_the_default_instances_are_closed(monkeypatch):
    llm, own_llm, source = _Closable(), _Closable(), _Closable()
    monkeypatch.setattr(llm_service, "_llm_service", llm)
    monkeypatch.setattr(metadata_service, "_metadata_service", _Metadata(own_llm))
    monkeypatch.setattr(input_source_service, "_input_source_service", source)

    await lifecycle.close_services()

    assert (llm.closed, own_llm.closed, source.closed) == (1, 1, 1)


async def test_closing_builds_no_instance(monkeypatch):
    monkeypatch.setattr(llm_service, "_llm_service", None)
    monkeypatch.setattr(metadata_service, "_metadata_service", None)
    monkeypatch.setattr(input_source_service, "_input_source_service", None)

    await lifecycle.close_services()

    assert llm_service._llm_service is None
    assert metadata_service._metadata_service is None
    assert input_source_service._input_source_service is None


def test_the_app_closes_the_services_when_it_stops(monkeypatch):
    calls = []

    async def close_services():
        calls.append("closed")

    monkeypatch.setattr(main, "close_services", close_services)

    with TestClient(main.app):
        assert calls == []

    assert calls == ["closed"]


def test_no_module_imports_a_private_name_of_another():
    private = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                private += [
                    f"{path.relative_to(SRC.parent)}:{node.lineno}: {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("_") and not alias.name.startswith("__")
                ]

    assert not private, private
