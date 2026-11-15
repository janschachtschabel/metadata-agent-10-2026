"""
Content type detection notices an answer cut off at the token limit (audit 14.11.2026, L-N).

extract_field reports finish_reason "length" as a failure; detect_content_type
did not look at it. A cut answer was "keine auswertbare Antwort", and the
warning of the keyword fallback gave no hint at the cause. An answer that is
complete in spite of the limit is still taken.
"""

import httpx
import pytest

from src.config import Settings
from src.services.llm_service import LLMService, reset_provider_gates
from src.services.metadata_service import MetadataService
from conftest import use_settings

CONTENT_TYPES = [
    {"schema_file": "event.json", "label": {"de": "Veranstaltung"}},
    {"schema_file": "learning_material.json", "label": {"de": "Lernmaterial"}},
]


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


def _service(monkeypatch, content, finish_reason):
    cfg = Settings(_env_file=None, b_api_key="own-key", llm_retry_delay=0)
    use_settings(monkeypatch, cfg)
    answer = {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}]
    }
    service = LLMService(llm_provider="b-api-openai")
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=answer))
    )
    return service


@pytest.mark.parametrize(
    "content",
    [
        '{"content_type": "event.js',
        '{"content_type": "learning_mat',
        "Der Text beschreibt eine Veranstaltung, daher ist der passende Inhaltstyp",
    ],
)
async def test_a_cut_answer_is_reported_as_cut(monkeypatch, content):
    service = _service(monkeypatch, content, "length")

    detection = await service.detect_content_type("Konzert am 12.11.", CONTENT_TYPES)

    assert detection.schema_file is None
    assert "abgeschnitten" in detection.problem
    assert "finish_reason=length" in detection.problem


async def test_a_complete_answer_at_the_limit_is_taken(monkeypatch):
    service = _service(monkeypatch, '{"content_type": "event.json"}', "length")

    detection = await service.detect_content_type("Konzert am 12.11.", CONTENT_TYPES)

    assert (detection.schema_file, detection.problem) == ("event.json", None)


async def test_an_unusable_answer_that_was_not_cut_keeps_its_reason(monkeypatch):
    service = _service(monkeypatch, "keine Ahnung", "stop")

    detection = await service.detect_content_type("Konzert am 12.11.", CONTENT_TYPES)

    assert detection.problem == "keine auswertbare Antwort"


async def test_the_fallback_warning_names_the_cut(monkeypatch):
    llm = _service(monkeypatch, '{"content_type": "event.js', "length")
    service = MetadataService.__new__(MetadataService)
    service.llm_service = llm

    detection = await service.detect_schema("Konzert am 12.11.", "default", "2.0.0")

    assert detection["method"] == "keywords"
    assert "abgeschnitten" in detection["reason"]
    assert "abgeschnitten" in detection["warning"]
