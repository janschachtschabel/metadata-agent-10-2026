"""
The B-API router as a fourth, optional LLM provider.

The router takes a route name (or `provider/model`) in the `model` field of
/api/v1/llm/router/chat/completions and forwards the request body unchanged to
whichever model of the route it picks. Two consequences pinned here:

- The body has to match the parameter family of the route's models: GPT-5/6
  want `max_completion_tokens` and no temperature, the others `max_tokens`.
  A route name says nothing about that, so it can be set explicitly.
- The router reports configuration errors that never heal themselves (unknown
  route, model without price, parameters the model rejects) as 400 or 503.
  Retrying them only wastes time; they end the extraction at once, like a
  refused key.

The other providers stay as they are; nothing here is the default.
"""

import json

import httpx
import pytest

from src.config import Settings
from src.services import llm_service as llm_module
from src.services.llm_auth import forwarded_authorization
from src.services.llm_errors import (
    LLMRateLimited,
    LLMRequestRejected,
    LLMRouteMisconfigured,
    LLMRouteNotFound,
    LLMUnreachable,
)
from src.services.llm_service import LLMService, reset_provider_gates

MESSAGES = [{"role": "user", "content": "x"}]
OK = {"choices": [{"message": {"content": '{"value": "ok"}'}}]}


class _Router:
    """A fake B-API router: one fixed answer, every request recorded."""

    def __init__(self, status=200, body=None):
        self.status, self.body = status, body
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.status == 200:
            return httpx.Response(200, json=self.body or OK)
        return httpx.Response(self.status, text=self.body or "")

    def sent(self, index=0) -> dict:
        return json.loads(self.calls[index].content)


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


def _settings(**overrides) -> Settings:
    defaults = {"b_api_key": "own-key", "llm_retry_delay": 0}
    return Settings(_env_file=None, **{**defaults, **overrides})


def _service(router, monkeypatch, model=None, **settings) -> LLMService:
    cfg = _settings(**settings)
    monkeypatch.setattr(llm_module, "get_settings", lambda: cfg)
    service = LLMService(llm_provider="b-api-router", llm_model=model)
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(router.handler)
    )
    return service


# ------------------------------------------------------------------ config


def test_the_router_is_its_own_provider_and_not_the_default():
    settings = _settings()
    config = settings.get_llm_config(provider_override="b-api-router")

    assert settings.llm_provider == "b-api-openai"
    assert config["provider"] == "b-api-router"
    assert (
        config["api_base"] == "https://b-api.staging.openeduhub.net/api/v1/llm/router"
    )
    assert config["limit_group"] == "b-api-router"
    assert config["requires_custom_header"] is True


def test_the_default_needs_no_route_to_exist():
    # provider/model is resolved by the router without any route: switching the
    # provider alone cannot break a running deployment.
    assert _settings().get_llm_config(provider_override="b-api-router")["model"] == (
        "openai/gpt-6-luna"
    )


def test_the_router_address_follows_the_b_api_base():
    settings = _settings(b_api_base_url="https://b-api.prod.openeduhub.net/")

    assert settings.get_llm_config(provider_override="b-api-router")["api_base"] == (
        "https://b-api.prod.openeduhub.net/api/v1/llm/router"
    )


def test_a_route_name_without_a_parameter_family_is_flagged():
    warnings = _settings(
        llm_provider="b-api-router", b_api_router_model="metadata-agent"
    ).config_warnings()

    assert any("LLM_REQUEST_STYLE" in w for w in warnings)


def test_a_route_name_with_a_parameter_family_is_fine():
    warnings = _settings(
        llm_provider="b-api-router",
        b_api_router_model="metadata-agent",
        llm_request_style="classic",
    ).config_warnings()

    assert not any("LLM_REQUEST_STYLE" in w for w in warnings)


# ------------------------------------------------------------------ requests


@pytest.mark.asyncio
async def test_the_route_name_goes_out_as_model_with_the_callers_key(monkeypatch):
    router = _Router()
    service = _service(
        router, monkeypatch, model="metadata-agent", llm_request_style="classic"
    )

    with forwarded_authorization("Bearer user-key"):
        await service._call_llm(MESSAGES)

    call = router.calls[0]
    assert call.url.path == "/api/v1/llm/router/chat/completions"
    assert router.sent()["model"] == "metadata-agent"
    assert call.headers["authorization"] == "Bearer user-key"
    assert "x-api-key" not in call.headers


@pytest.mark.asyncio
async def test_provider_slash_gpt_model_gets_the_reasoning_body(monkeypatch):
    router = _Router()
    service = _service(router, monkeypatch)  # default openai/gpt-6-luna, style auto

    await service._call_llm(MESSAGES)

    body = router.sent()
    assert "max_completion_tokens" in body
    assert "max_tokens" not in body and "temperature" not in body


@pytest.mark.asyncio
async def test_classic_family_gets_max_tokens_and_temperature(monkeypatch):
    router = _Router()
    service = _service(
        router, monkeypatch, model="schnell-guenstig", llm_request_style="classic"
    )

    await service._call_llm(MESSAGES)

    body = router.sent()
    assert "max_tokens" in body and "temperature" in body
    assert "max_completion_tokens" not in body


@pytest.mark.asyncio
async def test_reasoning_family_can_be_set_for_a_route_name(monkeypatch):
    router = _Router()
    service = _service(
        router, monkeypatch, model="chat-gpt", llm_request_style="reasoning"
    )

    await service._call_llm(MESSAGES)

    assert "max_completion_tokens" in router.sent()


@pytest.mark.asyncio
async def test_the_cache_can_be_bypassed(monkeypatch):
    router = _Router()
    service = _service(router, monkeypatch, b_api_ignore_cache=True)

    await service._call_llm(MESSAGES)

    assert router.calls[0].url.params["ignore-caching"] == "true"


@pytest.mark.asyncio
async def test_the_cache_is_used_by_default(monkeypatch):
    router = _Router()
    service = _service(router, monkeypatch)

    await service._call_llm(MESSAGES)

    assert "ignore-caching" not in router.calls[0].url.params


# ------------------------------------------------------------------ errors


@pytest.mark.asyncio
async def test_an_unknown_route_stops_at_once(monkeypatch):
    router = _Router(
        400, '{"error": "No route configured for model \'metadata-agent\'"}'
    )
    service = _service(
        router, monkeypatch, model="metadata-agent", llm_request_style="classic"
    )

    with pytest.raises(LLMRouteNotFound) as raised:
        await service._call_llm(MESSAGES)

    assert raised.value.fatal
    assert "metadata-agent" in raised.value.message
    assert len(router.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, body",
    [
        (
            400,
            '{"error": {"message": "Unsupported parameter: \'max_tokens\' is not supported with this model. Use \'max_completion_tokens\' instead."}}',
        ),
        (
            503,
            '{"error": "Model pricing unavailable for \'openai/gpt-6-lunaa\' - cannot enforce cost quota"}',
        ),
        (
            503,
            '{"error": "No deployment could serve model \'x\', attempts: luna:NOT_ELIGIBLE (HTTP 403)"}',
        ),
    ],
)
async def test_lasting_route_errors_are_not_retried(monkeypatch, status, body):
    router = _Router(status, body)
    service = _service(
        router, monkeypatch, model="metadata-agent", llm_request_style="classic"
    )

    with pytest.raises(LLMRouteMisconfigured) as raised:
        await service._call_llm(MESSAGES)

    assert raised.value.fatal
    assert len(router.calls) == 1


@pytest.mark.asyncio
async def test_a_wrong_family_names_the_setting(monkeypatch):
    router = _Router(
        400, '{"error": {"message": "Unsupported parameter: \'max_tokens\'"}}'
    )
    service = _service(
        router, monkeypatch, model="metadata-agent", llm_request_style="classic"
    )

    with pytest.raises(LLMRouteMisconfigured) as raised:
        await service._call_llm(MESSAGES)

    assert "LLM_REQUEST_STYLE" in raised.value.message


@pytest.mark.asyncio
async def test_other_providers_are_not_read_as_the_router(monkeypatch):
    """
    LLM_REQUEST_STYLE is ignored outside the router, so advising it there sends
    the operator nowhere - and a route error code would page for a route that
    does not exist. A refused parameter is an ordinary rejected request.
    """
    router = _Router(
        400, '{"error": {"message": "Unsupported parameter: \'max_tokens\'"}}'
    )
    cfg = _settings()
    monkeypatch.setattr(llm_module, "get_settings", lambda: cfg)
    service = LLMService(llm_provider="b-api-openai", llm_model="o9-preview")
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(router.handler)
    )

    with pytest.raises(LLMRequestRejected) as raised:
        await service._call_llm(MESSAGES)

    assert "LLM_REQUEST_STYLE" not in raised.value.message
    assert "max_tokens" in raised.value.message
    assert len(router.calls) == 1


@pytest.mark.asyncio
async def test_throttling_behind_the_router_is_rate_limiting(monkeypatch):
    # GWDG's 429 arrives as 503 "... RATE_LIMITED (HTTP 429)".
    router = _Router(
        503,
        '{"error": "No deployment could serve model \'x\', attempts: ac-1:RATE_LIMITED (HTTP 429)"}',
    )
    service = _service(
        router, monkeypatch, model="schnell-guenstig", llm_request_style="classic"
    )

    with pytest.raises(LLMRateLimited):
        await service._call_llm(MESSAGES)

    assert len(router.calls) == service.max_retries


@pytest.mark.asyncio
async def test_a_route_without_a_free_model_is_retried_then_unreachable(monkeypatch):
    router = _Router(
        503, '{"error": "No deployment could serve model \'x\' (no deployment left)"}'
    )
    service = _service(
        router, monkeypatch, model="schnell-guenstig", llm_request_style="classic"
    )

    with pytest.raises(LLMUnreachable) as raised:
        await service._call_llm(MESSAGES)

    assert raised.value.fatal
    assert len(router.calls) == service.max_retries


@pytest.mark.asyncio
async def test_the_request_style_applies_to_the_router_only(monkeypatch):
    # A request may pick another provider (llm_provider in the body). The style
    # set for the agent's route must not hand gpt-6-luna the classic body.
    router = _Router()
    cfg = _settings(llm_request_style="classic")
    monkeypatch.setattr(llm_module, "get_settings", lambda: cfg)
    service = LLMService(llm_provider="b-api-openai", llm_model="gpt-6-luna")
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(router.handler)
    )

    await service._call_llm(MESSAGES)

    body = router.sent()
    assert "max_completion_tokens" in body
    assert "temperature" not in body
