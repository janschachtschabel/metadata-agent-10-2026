"""
The ~50 field calls of one extraction read the page text from the prompt cache.

Measured through the B-API on 2026-10-16: the cache only hits where an earlier
prompt *ended*. A shared prefix inside a longer prompt was never read back, so
every field call was billed as a cache write (0.125 $ instead of 0.10 $ per 1M
tokens) and 0 tokens came from the cache. What works: the text as a message of
its own, and one primer call that ends exactly there before the fan-out — the
field calls then read 8,666 of 8,686 prompt tokens from the cache.
"""

import json

import httpx
import pytest

from src.config import Settings
from src.services import llm_service as llm_module
from src.services.llm_service import LLMService, reset_provider_gates
from src.utils.schema_loader import get_ai_fillable_fields, get_latest_version

TEXT = "Die Tage der Kammermusik bieten zwölf Konzerte in Weimar. " * 40


class _Gateway:
    def __init__(self, fail_primer=False):
        self.bodies = []
        self.fail_primer = fail_primer

    def handler(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.fail_primer and len(body["messages"]) == 2:
            return httpx.Response(500, text="primer failed")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"value": "Kammermusik"}'}}]},
        )


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


def _service(gateway, monkeypatch, provider="b-api-openai"):
    cfg = Settings(
        _env_file=None, b_api_key="own-key", llm_retry_delay=0, llm_max_retries=1
    )
    monkeypatch.setattr(llm_module, "get_settings", lambda: cfg)
    service = LLMService(llm_provider=provider)
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway.handler)
    )
    return service


def _fields(*ids):
    fields = get_ai_fillable_fields(
        "default", get_latest_version("default"), "core.json"
    )
    return [f for f in fields if f["id"] in ids]


@pytest.mark.asyncio
async def test_every_field_call_carries_the_text_as_a_message_of_its_own(monkeypatch):
    gateway = _Gateway()
    service = _service(gateway, monkeypatch)

    await service.extract_fields_parallel(
        _fields("cclom:title", "cclom:general_keyword"), TEXT
    )

    field_calls = [b for b in gateway.bodies if len(b["messages"]) == 3]
    assert len(field_calls) == 2
    text_messages = {json.dumps(b["messages"][:2], sort_keys=True) for b in field_calls}
    assert len(text_messages) == 1, "system prompt and text must be identical"
    assert TEXT in field_calls[0]["messages"][1]["content"]
    assert all(TEXT not in b["messages"][2]["content"] for b in field_calls)


@pytest.mark.asyncio
async def test_a_primer_ending_with_the_text_runs_before_the_field_calls(monkeypatch):
    gateway = _Gateway()
    service = _service(gateway, monkeypatch)

    await service.extract_fields_parallel(
        _fields("cclom:title", "cclom:general_keyword"), TEXT
    )

    primer, *field_calls = gateway.bodies
    assert len(primer["messages"]) == 2
    assert primer["messages"] == field_calls[0]["messages"][:2]
    assert len(field_calls) == 2


@pytest.mark.asyncio
async def test_no_primer_where_it_would_only_cost_quota(monkeypatch):
    # AcademicCloud shares a ~10 calls/min quota; an extra call per page hurts.
    gateway = _Gateway()
    service = _service(gateway, monkeypatch, provider="b-api-academiccloud")

    await service.extract_fields_parallel(
        _fields("cclom:title", "cclom:general_keyword"), TEXT
    )

    assert all(len(b["messages"]) == 3 for b in gateway.bodies)


@pytest.mark.asyncio
async def test_a_failing_primer_does_not_stop_the_extraction(monkeypatch):
    gateway = _Gateway(fail_primer=True)
    service = _service(gateway, monkeypatch)

    result = await service.extract_fields_parallel(_fields("cclom:title"), TEXT)

    assert [len(b["messages"]) for b in gateway.bodies] == [2, 3]
    assert not result.get("fatal")


def test_the_instruction_points_at_the_text_above():
    service = LLMService(llm_provider="b-api-openai")
    (field,) = _fields("cclom:general_keyword")

    assert "aus dem Text oben" in service._build_extraction_prompt(
        field, TEXT, None, "de"
    )
    assert "the text above" in service._build_extraction_prompt(field, TEXT, None, "en")


@pytest.mark.asyncio
async def test_a_failing_primer_is_not_retried(monkeypatch):
    # The primer is an optimisation; waiting out retries (up to Retry-After)
    # would only delay the field calls inside the 120 s budget.
    gateway = _Gateway(fail_primer=True)
    cfg = Settings(
        _env_file=None, b_api_key="own-key", llm_retry_delay=0, llm_max_retries=3
    )
    monkeypatch.setattr(llm_module, "get_settings", lambda: cfg)
    service = LLMService(llm_provider="b-api-openai")
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway.handler)
    )

    await service.extract_fields_parallel(_fields("cclom:title"), TEXT)

    assert [len(b["messages"]) for b in gateway.bodies] == [2, 3]


@pytest.mark.asyncio
async def test_an_empty_primer_answer_counts_as_done(monkeypatch, caplog):
    # gpt-6-luna answers 16 tokens with finish_reason "length" and no text
    # (measured 2026-10-16) — the cache is written all the same.
    class _EmptyPrimer(_Gateway):
        def handler(self, request):
            body = json.loads(request.content)
            if len(body["messages"]) == 2:
                self.bodies.append(body)
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"content": None}, "finish_reason": "length"}
                        ]
                    },
                )
            return super().handler(request)

    service = _service(_EmptyPrimer(), monkeypatch)
    caplog.set_level("INFO", logger="src.services.llm_service")

    await service.extract_fields_parallel(_fields("cclom:title"), TEXT)

    assert not [r for r in caplog.records if "primer" in r.getMessage().lower()]
