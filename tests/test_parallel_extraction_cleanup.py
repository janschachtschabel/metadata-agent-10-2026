"""
A fault in one field task of extract_fields_parallel cancels its siblings.

asyncio.gather without return_exceptions re-raises the first exception and
leaves the other field tasks running unattended - LLM calls nobody waits for,
holding gateway slots after the request has already failed.
"""

import asyncio

import pytest

from src.config import Settings
from src.services import llm_service as llm_module
from src.services.llm_service import LLMService, reset_provider_gates


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


async def test_a_failing_field_task_cancels_the_others(monkeypatch):
    cfg = Settings(_env_file=None, b_api_key="own-key", llm_retry_delay=0)
    monkeypatch.setattr(llm_module, "get_settings", lambda: cfg)
    service = LLMService(llm_provider="b-api-openai")
    started = asyncio.Event()
    cancelled = []

    async def no_primer(text, language):
        return None

    async def extract_field(field, text, existing_value, language):
        if field["id"] == "slow":
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(field["id"])
                raise
        await started.wait()
        raise RuntimeError("fault in the agent")

    monkeypatch.setattr(service, "_prime_text_cache", no_primer)
    monkeypatch.setattr(service, "extract_field", extract_field)

    with pytest.raises(RuntimeError):
        await service.extract_fields_parallel(
            [{"id": "slow"}, {"id": "broken"}], "Text", max_workers=2
        )

    assert cancelled == ["slow"]
