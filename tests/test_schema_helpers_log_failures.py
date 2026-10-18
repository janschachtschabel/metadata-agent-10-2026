"""
Schema helpers that fall back say so (final audit 18.10.).

get_latest_version answered a hard-coded "1.8.1", get_content_types [] and
get_content_type_prompt None for any fault, without a log line - and the
content-type detection then blamed the configuration ("für diesen Kontext ist
keine KI-Erkennung konfiguriert") for what was a broken schema file.
"""

import logging

import pytest

from src.services import metadata_service as metadata_module
from src.utils import schema_loader


def _broken(*args, **kwargs):
    raise OSError("schema file unreadable")


def test_latest_version_fallback_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(schema_loader, "load_context_registry", _broken)

    with caplog.at_level(logging.WARNING, logger=schema_loader.__name__):
        assert schema_loader.get_latest_version("default") == "1.8.1"

    assert "schema file unreadable" in caplog.text


def test_content_types_fallback_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(schema_loader, "load_schema", _broken)

    with caplog.at_level(logging.WARNING, logger=schema_loader.__name__):
        assert schema_loader.get_content_types("default", "2.0.0") == []

    assert "schema file unreadable" in caplog.text


def test_content_type_prompt_fallback_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(schema_loader, "load_schema", _broken)

    with caplog.at_level(logging.WARNING, logger=schema_loader.__name__):
        assert schema_loader.get_content_type_prompt("default", "2.0.0") is None

    assert "schema file unreadable" in caplog.text


class _NoLLM:
    provider, model, requires_custom_header = "b-api-openai", "m", False

    async def detect_content_type(self, *args, **kwargs):
        raise AssertionError("no LLM call without content types")


@pytest.mark.asyncio
async def test_detection_names_an_unreadable_schema_not_the_configuration(
    monkeypatch,
):
    monkeypatch.setattr(schema_loader, "load_schema", _broken)
    service = metadata_module.MetadataService.__new__(metadata_module.MetadataService)
    service.llm_service = _NoLLM()

    detection = await service.detect_schema("Ein Text.", "default", "2.0.0")

    assert detection["method"] == "keywords"
    assert "keine KI-Erkennung konfiguriert" not in detection["reason"]
    assert "nicht gelesen" in detection["reason"]
