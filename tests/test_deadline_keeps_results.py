"""
At the deadline the fields that are done are the answer.

Audit 13.11., P-11: one hanging field call was enough - a read timeout (90 s)
was tried three times, 273 s for a single call - and at 300 s the request
ended with 504 and nothing, although 51 of 52 fields had been there, and
paid for, after 20 s.
"""

import asyncio

import httpx
import pytest

from src.config import Settings
from src.services import llm_transport
from src.services.llm_errors import LLMUnreachable
from src.services.llm_service import (
    FIELD_RESERVE_SECONDS,
    LLMService,
    reset_provider_gates,
)
from src.services.metadata_generation import run_outcome
from conftest import use_settings


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


def _field(field_id):
    return {"id": field_id, "label": {"de": field_id}, "system": {"datatype": "string"}}


@pytest.mark.asyncio
async def test_fields_that_are_done_survive_a_call_that_hangs(monkeypatch):
    cancelled = []

    async def handler(request):
        if b"feld:haengt" in request.content:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"value": "Wert"}'},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    service = _service(monkeypatch, handler)
    fields = [_field("feld:a"), _field("feld:haengt"), _field("feld:b")]
    deadline = asyncio.get_running_loop().time() + FIELD_RESERVE_SECONDS + 0.5

    extraction = await service.extract_fields_parallel(
        fields, "Ein Text", deadline=deadline
    )

    assert extraction["values"] == {"feld:a": "Wert", "feld:b": "Wert"}
    assert extraction["timed_out"] == ["feld:haengt"]
    # The call nobody waits for any more is stopped, not left running.
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_without_a_deadline_every_field_is_awaited(monkeypatch):
    async def handler(request):
        await asyncio.sleep(0.05)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"value": "Wert"}'},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    service = _service(monkeypatch, handler)

    extraction = await service.extract_fields_parallel(
        [_field("feld:a"), _field("feld:b")], "Ein Text"
    )

    assert extraction["values"] == {"feld:a": "Wert", "feld:b": "Wert"}
    assert extraction["timed_out"] == []


def test_fields_cut_off_by_the_deadline_count_as_failed():
    extraction = {"errors": [], "skipped": 0, "timed_out": ["a", "b"]}

    outcome = run_outcome(extraction, {"cclom:title": "Titel"})

    assert (outcome["status"], outcome["fields_failed"]) == ("partial", 2)


@pytest.mark.asyncio
async def test_a_read_timeout_is_tried_once_more_not_three_times(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("")

    service = _service(monkeypatch, handler, llm_max_retries=3)

    with pytest.raises(LLMUnreachable):
        await service._call_llm(messages=[{"role": "user", "content": "x"}])

    assert len(calls) == llm_transport.READ_TIMEOUT_ATTEMPTS == 2


@pytest.mark.asyncio
async def test_a_refused_connection_is_still_tried_as_often_as_configured(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("")

    service = _service(monkeypatch, handler, llm_max_retries=3)

    with pytest.raises(LLMUnreachable):
        await service._call_llm(messages=[{"role": "user", "content": "x"}])

    assert len(calls) == 3
