"""
Who an LLM failure is for: the operator, or the caller.

A refused key or an unknown route is an operator problem when it is the
agent's own key and its configured model - and a caller's problem when the
B-API forwarded the caller's key, or the request picked its own provider or
model. The critical alerts (helm/metadata-agent/files/alerts.yaml) may only
fire for the first, or any caller with a typo pages operations. Hence the two
labels on metadata_agent_llm_requests_total:

- auth_source: forwarded | static | openai | none (no key at all)
- config: default (the configured provider and model) | override
"""

import httpx
import pytest
from prometheus_client import REGISTRY

from src.config import Settings
from src.services.llm_auth import forwarded_authorization
from src.services.llm_errors import LLMAuthForbidden, LLMCredentialsMissing
from src.services.llm_service import LLMService, reset_provider_gates
from conftest import use_settings

MESSAGES = [{"role": "user", "content": "x"}]
OK = {"choices": [{"message": {"content": '{"value": "ok"}'}}]}


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


def _service(monkeypatch, response, provider=None, model=None, **settings):
    cfg = Settings(
        _env_file=None, **{"b_api_key": "own-key", "llm_retry_delay": 0, **settings}
    )
    use_settings(monkeypatch, cfg)
    service = LLMService(llm_provider=provider, llm_model=model)
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: response)
    )
    return service


def _count(**labels) -> float:
    return REGISTRY.get_sample_value("metadata_agent_llm_requests_total", labels) or 0.0


@pytest.mark.asyncio
async def test_a_forwarded_key_that_is_refused_is_the_callers_problem(monkeypatch):
    service = _service(monkeypatch, httpx.Response(403, text="Forbidden"))
    labels = {
        "provider": "b-api-openai",
        "result": "LLM_AUTH_FORBIDDEN",
        "auth_source": "forwarded",
        "config": "default",
    }
    before = _count(**labels)

    with forwarded_authorization("Bearer caller-key"):
        with pytest.raises(LLMAuthForbidden):
            await service._call_llm(MESSAGES)

    assert _count(**labels) == before + 1


@pytest.mark.asyncio
async def test_the_own_key_with_a_model_the_request_picked_is_an_override(
    monkeypatch,
):
    service = _service(
        monkeypatch, httpx.Response(200, json=OK), model="gpt-6-luna-mini"
    )
    labels = {
        "provider": "b-api-openai",
        "result": "ok",
        "auth_source": "static",
        "config": "override",
    }
    before = _count(**labels)

    await service._call_llm(MESSAGES)

    assert _count(**labels) == before + 1


@pytest.mark.asyncio
async def test_a_request_that_names_the_configured_model_is_still_default(
    monkeypatch,
):
    service = _service(
        monkeypatch,
        httpx.Response(200, json=OK),
        provider="b-api-openai",
        model="gpt-6-luna",
    )
    labels = {
        "provider": "b-api-openai",
        "result": "ok",
        "auth_source": "static",
        "config": "default",
    }
    before = _count(**labels)

    await service._call_llm(MESSAGES)

    assert _count(**labels) == before + 1


@pytest.mark.asyncio
async def test_no_key_at_all_is_counted_as_none(monkeypatch):
    service = _service(
        monkeypatch, httpx.Response(200, json=OK), b_api_auth_mode="forward"
    )
    labels = {
        "provider": "b-api-openai",
        "result": "LLM_AUTH_MISSING",
        "auth_source": "none",
        "config": "default",
    }
    before = _count(**labels)

    with pytest.raises(LLMCredentialsMissing):
        await service._call_llm(MESSAGES)

    assert _count(**labels) == before + 1


@pytest.mark.asyncio
async def test_native_openai_uses_the_operators_key(monkeypatch):
    service = _service(
        monkeypatch,
        httpx.Response(200, json=OK),
        llm_provider="openai",
        openai_api_key="sk-test",
    )
    labels = {
        "provider": "openai",
        "result": "ok",
        "auth_source": "openai",
        "config": "default",
    }
    before = _count(**labels)

    await service._call_llm(MESSAGES)

    assert _count(**labels) == before + 1
