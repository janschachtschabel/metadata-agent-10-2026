"""
The current B-API forwards a delegation token instead of the caller's key
(forwardDelegation, 2026-11-10): `Authorization: Bearer bdt_...` by default, or
the raw token in the header the proxy entry names in `delegationHeader`.

The agent passes it on unchanged, per request, as `Authorization: Bearer ...`.
Without METADATA_AGENT_B_API_DELEGATION_HEADER nothing changes: the agent reads
`Authorization` as before (forwardApiKey and operation without the B-API).
"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.config import Settings
from src.services import input_source_service as input_module
from src.services.input_source_service import InputSourceService, TextExtractionError
from src.services.llm_auth import (
    ForwardedAuthorizationMiddleware,
    forwarded_authorization,
    forwarded_credential,
)
from conftest import use_settings

TOKEN = "bdt_0123456789abcdef"


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{"b_api_key": "own-key", **overrides})


def _seen_by_the_agent(monkeypatch, settings, headers) -> object:
    """What the middleware makes of `headers` - the forwarded credential."""
    use_settings(monkeypatch, settings)
    app = FastAPI()

    @app.get("/probe")
    def probe():
        return {"forwarded": forwarded_credential()}

    app.add_middleware(ForwardedAuthorizationMiddleware)
    with TestClient(app) as client:
        return client.get("/probe", headers=headers).json()["forwarded"]


def test_without_a_delegation_header_authorization_is_read_as_before(monkeypatch):
    seen = _seen_by_the_agent(
        monkeypatch,
        _settings(),
        {"Authorization": f"Bearer {TOKEN}", "X-Delegation": "other"},
    )

    assert seen == f"Bearer {TOKEN}"


def test_a_configured_delegation_header_carries_the_raw_token(monkeypatch):
    seen = _seen_by_the_agent(
        monkeypatch,
        _settings(b_api_delegation_header="X-Delegation"),
        {"x-delegation": TOKEN},
    )

    assert seen == f"Bearer {TOKEN}"


def test_a_bearer_value_in_the_delegation_header_stays_as_it_is(monkeypatch):
    seen = _seen_by_the_agent(
        monkeypatch,
        _settings(b_api_delegation_header="X-Delegation"),
        {"X-Delegation": f"Bearer {TOKEN}"},
    )

    assert seen == f"Bearer {TOKEN}"


def test_without_the_delegation_header_authorization_still_counts(monkeypatch):
    """A proxy entry still on forwardApiKey keeps working."""
    seen = _seen_by_the_agent(
        monkeypatch,
        _settings(b_api_delegation_header="X-Delegation"),
        {"Authorization": "Bearer caller-key"},
    )

    assert seen == "Bearer caller-key"


@pytest.mark.parametrize("name", ["X-API-KEY", "with space", "bad:colon", "x" * 80])
def test_a_delegation_header_name_that_cannot_be_one_stops_the_start(name):
    with pytest.raises(ValidationError):
        _settings(b_api_delegation_header=name)


async def _public(url):
    return url


@pytest.mark.asyncio
async def test_a_refused_token_is_named_as_token_or_scope(monkeypatch):
    """401 of a token: unknown, expired, call ended or outside its scopes."""
    use_settings(monkeypatch, _settings(b_api_services="proxy", b_api_key=""))
    monkeypatch.setattr(input_module, "require_public_http_url", _public)
    service = InputSourceService()
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(401))
    )

    with forwarded_authorization(f"Bearer {TOKEN}"):
        with pytest.raises(TextExtractionError) as raised:
            await service.fetch_from_url("https://example.org/page")

    assert raised.value.code == "TEXT_EXTRACTION_AUTH_REFUSED"
    assert "Scope" in raised.value.message and "Token" in raised.value.message
    assert TOKEN not in raised.value.message
