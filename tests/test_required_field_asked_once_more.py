"""
A required field without a value is asked for once more, not llm_max_retries times (audit 14.11.2026, L-46).

A text that has no value for a required field cost 1 + llm_max_retries calls
for it (four by default; source.json eight instead of two per run). One more
call is enough to look closer, and that call is told it is the last one - null
is then an answer. A required field with a closed vocabulary gets no second
call at all: null there says that no concept fits, and an invalid concept is
asked again by the vocabulary check as before.
"""

import json

import httpx
import pytest

from src.config import Settings
from src.services.llm_service import LLMService, reset_provider_gates
from conftest import use_settings

TITLE = {
    "id": "cclom:title",
    "label": {"de": "Titel"},
    "system": {"datatype": "string", "required": True},
}
MODE = {
    "id": "harv:mode",
    "label": {"de": "Modus"},
    "system": {
        "datatype": "string",
        "required": True,
        "vocabulary": {
            "type": "closed",
            "concepts": [
                {"value": "full", "label": {"de": "Vollständig"}},
                {"value": "delta", "label": {"de": "Änderungen"}},
            ],
        },
    },
}
OPTIONAL = {
    "id": "cclom:general_keyword",
    "label": {"de": "Schlagwörter"},
    "system": {"datatype": "string"},
}


class _Gateway:
    """Answers the calls in turn with `answers`, the last one for all further calls."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def handler(self, request):
        self.prompts.append(json.loads(request.content)["messages"][-1]["content"])
        answer = self.answers[min(len(self.prompts), len(self.answers)) - 1]
        content = json.dumps({"value": answer})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


def _service(monkeypatch, gateway, **settings):
    cfg = Settings(
        _env_file=None, **{"b_api_key": "own-key", "llm_retry_delay": 0, **settings}
    )
    use_settings(monkeypatch, cfg)
    service = LLMService(llm_provider="b-api-openai")
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway.handler)
    )
    return service


@pytest.mark.parametrize("llm_max_retries", [1, 3, 5])
async def test_a_required_field_without_a_value_costs_two_calls(
    monkeypatch, llm_max_retries
):
    gateway = _Gateway(None)
    service = _service(monkeypatch, gateway, llm_max_retries=llm_max_retries)

    _, value, error = await service.extract_field(TITLE, "Text")

    assert (value, error) == (None, None)
    assert len(gateway.prompts) == 2


async def test_the_second_call_is_told_it_is_the_last(monkeypatch):
    gateway = _Gateway(None)
    service = _service(monkeypatch, gateway)

    await service.extract_field(TITLE, "Text")

    first, second = gateway.prompts
    assert "VERSUCH" not in first
    assert "VERSUCH 2" in second
    assert "LETZTER VERSUCH" in second
    assert "null zurückgeben" in second


async def test_the_value_of_the_second_call_is_taken(monkeypatch):
    gateway = _Gateway(None, "Ein Titel")
    service = _service(monkeypatch, gateway)

    _, value, _ = await service.extract_field(TITLE, "Text")

    assert value == "Ein Titel"
    assert len(gateway.prompts) == 2


async def test_a_required_closed_field_without_a_value_costs_one_call(monkeypatch):
    gateway = _Gateway(None)
    service = _service(monkeypatch, gateway)

    _, value, error = await service.extract_field(MODE, "Text")

    assert (value, error) == (None, None)
    assert len(gateway.prompts) == 1


async def test_an_invalid_concept_is_still_asked_again_as_often_as_configured(
    monkeypatch,
):
    gateway = _Gateway("irgendwas anderes")
    service = _service(monkeypatch, gateway, llm_max_retries=3)

    _, value, _ = await service.extract_field(MODE, "Text")

    assert value is None
    assert len(gateway.prompts) == 4


async def test_an_optional_field_without_a_value_costs_one_call(monkeypatch):
    gateway = _Gateway(None)
    service = _service(monkeypatch, gateway)

    await service.extract_field(OPTIONAL, "Text")

    assert len(gateway.prompts) == 1
