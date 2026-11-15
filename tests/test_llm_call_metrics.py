"""
What the two LLM metrics say about a call (audit 14.11.2026).

- L-36: a call stopped at the deadline of the run or because the caller went
  away was counted as result="ok" - three hanging calls made the gateway look
  healthy. It is counted as result="cancelled".
- L-N: metadata_agent_llm_request_duration_seconds is the time a call takes
  for the extraction that waits for it: the wait for a slot at the provider
  gate, the request, retries and the pauses between them. Pinned here so the
  help text of the metric (src/metrics.py) can say so.
"""

import asyncio

import httpx
import pytest
from prometheus_client import REGISTRY

from src.config import Settings
from src.metrics import LLM_DURATION
from src.services.llm_service import (
    FIELD_RESERVE_SECONDS,
    LLMService,
    reset_provider_gates,
)
from conftest import use_settings

MESSAGES = [{"role": "user", "content": "x"}]
OK = {"choices": [{"message": {"content": '{"value": "ok"}'}}]}


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


def _service(monkeypatch, handler, **settings) -> LLMService:
    cfg = Settings(
        _env_file=None, **{"b_api_key": "own-key", "llm_retry_delay": 0, **settings}
    )
    use_settings(monkeypatch, cfg)
    service = LLMService(llm_provider="b-api-openai")
    service.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return service


def _requests(result: str) -> float:
    labels = {
        "provider": "b-api-openai",
        "result": result,
        "auth_source": "static",
        "config": "default",
    }
    return REGISTRY.get_sample_value("metadata_agent_llm_requests_total", labels) or 0.0


def _duration(sample: str) -> float:
    name = f"metadata_agent_llm_request_duration_seconds_{sample}"
    return REGISTRY.get_sample_value(name, {"provider": "b-api-openai"}) or 0.0


async def _hang(request):
    await asyncio.sleep(30)
    return httpx.Response(200, json=OK)


async def test_calls_stopped_at_the_deadline_are_counted_as_cancelled(monkeypatch):
    service = _service(monkeypatch, _hang)
    fields = [
        {"id": f"feld:{i}", "label": {"de": "x"}, "system": {"datatype": "string"}}
        for i in range(3)
    ]
    ok, cancelled = _requests("ok"), _requests("cancelled")
    deadline = asyncio.get_running_loop().time() + FIELD_RESERVE_SECONDS + 0.3

    extraction = await service.extract_fields_parallel(fields, "T", deadline=deadline)

    assert len(extraction["timed_out"]) == 3
    assert _requests("cancelled") == cancelled + 3
    assert _requests("ok") == ok


async def test_a_call_whose_caller_went_away_is_counted_as_cancelled(monkeypatch):
    service = _service(monkeypatch, _hang)
    ok, cancelled = _requests("ok"), _requests("cancelled")

    call = asyncio.ensure_future(service._call_llm(MESSAGES))
    await asyncio.sleep(0.05)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    assert _requests("cancelled") == cancelled + 1
    assert _requests("ok") == ok


async def test_an_answered_call_is_still_ok(monkeypatch):
    service = _service(monkeypatch, lambda request: httpx.Response(200, json=OK))
    ok, cancelled = _requests("ok"), _requests("cancelled")

    await service._call_llm(MESSAGES)

    assert _requests("ok") == ok + 1
    assert _requests("cancelled") == cancelled


async def test_the_duration_includes_the_wait_at_the_provider_gate(monkeypatch):
    # Two calls per second: the second call waits half a second for its slot.
    service = _service(monkeypatch, lambda request: httpx.Response(200, json=OK))
    service.requests_per_second = 2.0
    count, total = _duration("count"), _duration("sum")

    await asyncio.gather(service._call_llm(MESSAGES), service._call_llm(MESSAGES))

    assert _duration("count") == count + 2
    assert _duration("sum") - total >= 0.45


def test_the_help_text_says_what_the_duration_measures():
    text = LLM_DURATION._documentation

    for part in ("wait for a slot", "retries", "pauses"):
        assert part in text, text
