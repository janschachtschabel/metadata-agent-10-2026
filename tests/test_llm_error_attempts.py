"""
An LLM error names the number of attempts that were really made (audit 14.11.2026, L-N).

The messages said "nach 3 Versuchen" (llm_max_retries) also when the request
had been sent once: for a status that is not retried (501), and for a call
with its own number of attempts.
"""

import httpx
import pytest

from src.config import Settings
from src.services.llm_errors import LLMUnreachable, LLMUpstreamError
from src.services.llm_service import LLMService, reset_provider_gates
from conftest import use_settings

MESSAGES = [{"role": "user", "content": "x"}]


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


def _service(monkeypatch, handler, **settings):
    cfg = Settings(
        _env_file=None, **{"b_api_key": "own-key", "llm_retry_delay": 0, **settings}
    )
    use_settings(monkeypatch, cfg)
    service = LLMService(llm_provider="b-api-openai")
    calls = []

    def counting(request):
        calls.append(request)
        return handler(request)

    service.http_client = httpx.AsyncClient(transport=httpx.MockTransport(counting))
    return service, calls


async def test_a_status_that_is_not_retried_was_tried_once(monkeypatch):
    service, calls = _service(monkeypatch, lambda r: httpx.Response(501, text="no"))

    with pytest.raises(LLMUpstreamError) as raised:
        await service._call_llm(MESSAGES)

    assert len(calls) == 1
    assert "nach 1 Versuch mit HTTP 501" in raised.value.message


@pytest.mark.parametrize(
    "llm_max_retries, words", [(3, "3 Versuchen"), (2, "2 Versuchen")]
)
async def test_a_retried_status_names_every_attempt(
    monkeypatch, llm_max_retries, words
):
    service, calls = _service(
        monkeypatch,
        lambda r: httpx.Response(503, text="busy"),
        llm_max_retries=llm_max_retries,
    )

    with pytest.raises(LLMUpstreamError) as raised:
        await service._call_llm(MESSAGES)

    assert len(calls) == llm_max_retries
    assert f"nach {words} mit HTTP 503" in raised.value.message


async def test_a_call_with_one_attempt_says_so(monkeypatch):
    service, calls = _service(monkeypatch, lambda r: httpx.Response(503, text="busy"))

    with pytest.raises(LLMUpstreamError) as raised:
        await service._send_llm_request(MESSAGES, attempts=1)

    assert len(calls) == 1
    assert "nach 1 Versuch mit HTTP 503" in raised.value.message


async def test_a_200_without_json_names_its_attempts(monkeypatch):
    service, calls = _service(
        monkeypatch,
        lambda r: httpx.Response(
            200, text="<html>", headers={"content-type": "text/html"}
        ),
    )

    with pytest.raises(LLMUpstreamError) as raised:
        await service._send_llm_request(MESSAGES, attempts=2)

    assert len(calls) == 2
    assert "nach 2 Versuchen mit HTTP 200, aber ohne JSON" in raised.value.message


async def test_a_read_timeout_names_its_two_attempts(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("zu langsam", request=request)

    service, calls = _service(monkeypatch, timeout)

    with pytest.raises(LLMUnreachable) as raised:
        await service._call_llm(MESSAGES)

    assert len(calls) == 2
    assert "nach 2 Versuchen: ReadTimeout" in raised.value.message
