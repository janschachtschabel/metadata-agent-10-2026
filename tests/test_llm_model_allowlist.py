"""
Which models a request may name (audit 18.10. final, S-19).

`llm_model` per request runs on the server's key wherever no caller key is
forwarded. METADATA_AGENT_ALLOWED_LLM_MODELS lets an operator restrict it;
empty (the default) keeps 2.1.1's behaviour: every model name is passed on.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.config import Settings
from conftest import use_settings


@pytest.fixture
def allow(monkeypatch):
    def configure(models: str) -> None:
        use_settings(monkeypatch, Settings(allowed_llm_models=models))

    return configure


def _generate(model: str):
    with TestClient(main.app) as client:
        return client.post(
            "/generate",
            json={"input_source": "text", "text": "Ein Text.", "llm_model": model},
        )


def test_a_model_outside_the_list_is_refused_before_any_llm_call(allow, monkeypatch):
    allow("gpt-6-luna, gpt-5-mini")

    def no_llm(**kwargs):
        raise AssertionError("no LLM client may be built")

    monkeypatch.setattr(deps, "_get_metadata_service", no_llm)

    response = _generate("o3-pro")

    assert response.status_code == 422
    body = response.json()
    assert "o3-pro" in body["detail"] and body["error"]["code"]
    # The public answer names no setting.
    assert "ALLOWED_LLM_MODELS" not in response.text


@pytest.mark.parametrize("model", ["gpt-5-mini", "gpt-6-luna"])
def test_listed_models_and_the_configured_default_pass(allow, model):
    allow("gpt-5-mini")

    service = deps.get_llm_service(llm_model=model)

    assert service.model == model


def test_without_a_list_every_model_passes(allow):
    allow("")

    assert (
        deps.get_llm_service(llm_model="irgendein-modell").model == "irgendein-modell"
    )


def test_the_list_reads_like_the_other_list_settings():
    settings = Settings(allowed_llm_models=" a , b,,c ")

    assert settings.allowed_llm_model_names() == {"a", "b", "c"}
