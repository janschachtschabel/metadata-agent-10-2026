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
from src.services.llm_service import LLMService, reset_provider_gates
from src.utils.schema_loader import get_ai_fillable_fields, get_latest_version
from conftest import use_settings

# Above the primer's minimum length (_PRIMER_MIN_TEXT_CHARS): a shorter prefix
# stays below the ~1,024-token cache minimum and a primer would only cost a call.
TEXT = "Die Tage der Kammermusik bieten zwölf Konzerte in Weimar. " * 80
TWO_FIELDS = ("cclom:title", "cclom:general_keyword")


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


def _service(gateway, monkeypatch, provider="b-api-openai", model=None, **settings):
    cfg = Settings(
        _env_file=None,
        b_api_key="own-key",
        llm_retry_delay=0,
        llm_max_retries=1,
        **settings,
    )
    use_settings(monkeypatch, cfg)
    service = LLMService(llm_provider=provider, llm_model=model)
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway.handler)
    )
    return service


def _text_part(body):
    """The one content part of the text message, as gpt-6 models get it."""
    (part,) = body["messages"][1]["content"]
    return part


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
    assert TEXT in _text_part(field_calls[0])["text"]
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

    result = await service.extract_fields_parallel(_fields(*TWO_FIELDS), TEXT)

    assert [len(b["messages"]) for b in gateway.bodies] == [2, 3, 3]
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
    # would only delay the field calls inside the /generate budget.
    gateway = _Gateway(fail_primer=True)
    cfg = Settings(
        _env_file=None, b_api_key="own-key", llm_retry_delay=0, llm_max_retries=3
    )
    use_settings(monkeypatch, cfg)
    service = LLMService(llm_provider="b-api-openai")
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway.handler)
    )

    await service.extract_fields_parallel(_fields(*TWO_FIELDS), TEXT)

    assert [len(b["messages"]) for b in gateway.bodies] == [2, 3, 3]


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

    gateway = _EmptyPrimer()
    service = _service(gateway, monkeypatch)
    caplog.set_level("INFO", logger="src.services.llm_service")

    await service.extract_fields_parallel(_fields(*TWO_FIELDS), TEXT)

    assert len(gateway.bodies[0]["messages"]) == 2  # the primer did run

    assert not [r for r in caplog.records if "primer" in r.getMessage().lower()]


# ------------------------------------- final audit 18.10.: when to prime


@pytest.mark.asyncio
async def test_no_primer_for_a_single_field(monkeypatch):
    # regenerate_fields=[x]: the primer would double the calls for nothing.
    gateway = _Gateway()
    service = _service(gateway, monkeypatch)

    await service.extract_fields_parallel(_fields("cclom:title"), TEXT)

    assert [len(b["messages"]) for b in gateway.bodies] == [3]


@pytest.mark.asyncio
async def test_no_primer_for_a_text_below_the_cache_minimum(monkeypatch):
    gateway = _Gateway()
    service = _service(gateway, monkeypatch)

    await service.extract_fields_parallel(_fields(*TWO_FIELDS), "Kurzer Text. " * 50)

    assert [len(b["messages"]) for b in gateway.bodies] == [3, 3]


# ------------------- 15.11.: an explicit cache breakpoint after the text
#
# Measured through the B-API (gpt-6-luna): without it every field call writes
# its whole prompt to the cache at 1.25 times the input price - also the field
# instruction, which no later call reads. With the breakpoint the primer writes
# the text once (cache_write 3882) and the field calls only read it (cached
# 3882, cache_write 0).

BREAKPOINT = {"mode": "explicit"}


@pytest.mark.asyncio
async def test_the_text_ends_with_an_explicit_cache_breakpoint(monkeypatch):
    gateway = _Gateway()
    service = _service(gateway, monkeypatch)

    await service.extract_fields_parallel(_fields(*TWO_FIELDS), TEXT)

    primer, *field_calls = gateway.bodies
    assert len(field_calls) == 2
    for body in gateway.bodies:  # the primer has to write what the fields look up
        assert body["prompt_cache_options"] == BREAKPOINT
        part = _text_part(body)
        assert part["type"] == "text" and TEXT in part["text"]
        assert part["prompt_cache_breakpoint"] == BREAKPOINT
    assert primer["messages"] == field_calls[0]["messages"][:2]
    assert all(isinstance(b["messages"][2]["content"], str) for b in field_calls)


@pytest.mark.asyncio
async def test_the_router_marks_the_text_for_its_openai_model(monkeypatch):
    gateway = _Gateway()
    service = _service(gateway, monkeypatch, "b-api-router", "openai/gpt-6-luna")

    await service.extract_fields_parallel(_fields(*TWO_FIELDS), TEXT)

    assert [b.get("prompt_cache_options") for b in gateway.bodies] == [BREAKPOINT] * 3


@pytest.mark.parametrize(
    "provider, model",
    [
        # Both answered 400 "prompt_cache_options is not supported on this model".
        ("b-api-openai", "gpt-4.1-mini"),
        ("b-api-openai", "gpt-5.4-mini"),
        ("b-api-academiccloud", None),
        ("b-api-router", "eine-route"),
    ],
)
@pytest.mark.asyncio
async def test_no_breakpoint_for_models_that_refuse_it(monkeypatch, provider, model):
    gateway = _Gateway()
    service = _service(gateway, monkeypatch, provider, model)

    await service.extract_fields_parallel(_fields(*TWO_FIELDS), TEXT)

    assert gateway.bodies
    for body in gateway.bodies:
        assert "prompt_cache_options" not in body
        assert isinstance(body["messages"][1]["content"], str)


@pytest.mark.asyncio
async def test_the_breakpoint_can_be_switched_off(monkeypatch):
    gateway = _Gateway()
    service = _service(gateway, monkeypatch, llm_cache_breakpoint="off")

    await service.extract_fields_parallel(_fields(*TWO_FIELDS), TEXT)

    assert len(gateway.bodies) == 3  # the primer stays: the implicit cache needs it
    for body in gateway.bodies:
        assert "prompt_cache_options" not in body
        assert isinstance(body["messages"][1]["content"], str)


@pytest.mark.asyncio
async def test_the_breakpoint_can_be_forced_for_a_model_the_rule_does_not_know(
    monkeypatch,
):
    gateway = _Gateway()
    service = _service(
        gateway, monkeypatch, model="gpt-7-nova", llm_cache_breakpoint="on"
    )

    await service.extract_fields_parallel(_fields(*TWO_FIELDS), TEXT)

    assert [b.get("prompt_cache_options") for b in gateway.bodies] == [BREAKPOINT] * 3


def test_forcing_the_breakpoint_never_reaches_academiccloud(monkeypatch):
    service = _service(
        _Gateway(), monkeypatch, "b-api-academiccloud", llm_cache_breakpoint="on"
    )

    assert isinstance(service._text_messages(TEXT, "de")[1]["content"], str)


def test_a_call_without_a_breakpoint_keeps_the_implicit_cache(monkeypatch):
    # Type detection and normalisation: no marked text, no explicit mode.
    service = _service(_Gateway(), monkeypatch)

    body = service._request_body([{"role": "user", "content": "x"}], None, None)

    assert "prompt_cache_options" not in body
