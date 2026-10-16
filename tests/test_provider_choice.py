"""
Which LLM provider a request may pick.

A provider name the service does not know used to fall through to native
OpenAI with the server's OPENAI_API_KEY. That sent the caller's text to
api.openai.com and bypassed B_API_AUTH_MODE=forward, which exists so that
calls are billed to the caller's own key.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src import main
from src.config import Settings
from src.models.schemas import (
    DetectContentTypeRequest,
    ExtractFieldRequest,
    GenerateRequest,
)

REQUESTS = [
    (GenerateRequest, {"text": "x"}),
    (DetectContentTypeRequest, {"text": "x"}),
    (
        ExtractFieldRequest,
        {"text": "x", "field_id": "cclom:title", "schema_file": "core.json"},
    ),
]


@pytest.mark.parametrize("model, base", REQUESTS)
@pytest.mark.parametrize("name", ["opnai", "anthropic", "b-api-foo"])
def test_an_unknown_provider_is_refused(model, base, name):
    with pytest.raises(ValidationError):
        model(**base, llm_provider=name)


@pytest.mark.parametrize("model, base", REQUESTS)
@pytest.mark.parametrize(
    "name", ["b-api-openai", "b-api-academiccloud", "b-api-router"]
)
def test_the_b_api_providers_can_be_picked(model, base, name):
    assert model(**base, llm_provider=name).llm_provider == name


def test_openai_is_refused_when_the_service_runs_on_the_b_api(monkeypatch):
    monkeypatch.setattr(main.settings, "llm_provider", "b-api-openai")

    response = TestClient(main.app).post(
        "/generate", json={"text": "Ein Text", "llm_provider": "openai"}
    )

    assert response.status_code == 422
    assert "openai" in response.text


def test_openai_stays_available_where_it_is_the_configured_provider(monkeypatch):
    monkeypatch.setattr(main.settings, "llm_provider", "openai")

    assert main.check_provider_override("openai") is None


def test_a_typo_in_the_configured_provider_fails_at_startup():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_provider="b-api-opnai")


def test_the_configured_provider_ignores_case_and_spaces():
    assert Settings(_env_file=None, llm_provider=" B-API-Router ").llm_provider == (
        "b-api-router"
    )


@pytest.mark.parametrize("model, base", REQUESTS)
def test_the_request_ignores_case_and_spaces_like_the_setting(model, base):
    assert model(**base, llm_provider=" B-API-OpenAI ").llm_provider == "b-api-openai"
