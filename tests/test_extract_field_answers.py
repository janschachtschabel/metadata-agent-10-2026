"""
How extract_field treats single model answers (release review 2.1.1 -> 3.0.0).

- #13 dpt:concept is a uri field with a skos vocabulary: the uri check ran
  before the vocabulary check, so an exact label counted as invalid (4 calls
  instead of 1) and an invalid label survived the last attempt.
- #14 {"value": ""} for an optional closed field is "no value", like null -
  it was counted as an invalid concept and asked again llm_max_retries times.
- #16 a 200 whose body is no JSON (an HTML page from a proxy) raised a plain
  ValueError: "Internal error", a stack trace per field, no retry.
- Tracing headers (B3, x-request-id) go to the agent's own services only;
  the native OpenAI provider talks to a third party.
"""

import json

import httpx
import pytest

from src.config import Settings
from src.metrics import propagate_trace
from src.services import llm_service as llm_module
from src.services.llm_errors import LLMUpstreamError
from src.services.llm_service import LLMService, reset_provider_gates
from src.utils.schema_loader import get_ai_fillable_fields, get_latest_version


class _Gateway:
    """Answers every call with the same content, or with a non-JSON body."""

    def __init__(self, answer=None, html=False):
        self.answer, self.html = answer, html
        self.calls = 0

    def handler(self, request):
        self.calls += 1
        if self.html:
            return httpx.Response(
                200,
                text="<html><body>Proxy login</body></html>",
                headers={"content-type": "text/html"},
            )
        content = json.dumps({"value": self.answer})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


def _settings(monkeypatch, **overrides):
    values = {"b_api_key": "own-key", "llm_retry_delay": 0, **overrides}
    cfg = Settings(_env_file=None, **values)
    monkeypatch.setattr(llm_module, "get_settings", lambda: cfg)
    return cfg


def _service(gateway, monkeypatch):
    _settings(monkeypatch)
    service = LLMService(llm_provider="b-api-openai")
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway.handler)
    )
    return service


def _field(schema_file, field_id):
    fields = get_ai_fillable_fields(
        "default", get_latest_version("default"), schema_file
    )
    return next(f for f in fields if f["id"] == field_id)


def _concept(field, index=0):
    return field["system"]["vocabulary"]["concepts"][index]


# ------------------------------------------------------------ #13 uri + skos


async def test_an_exact_label_of_a_uri_skos_field_is_accepted_at_once(monkeypatch):
    field = _field("didactic_planning_tools.json", "dpt:concept")
    concept = _concept(field)
    gateway = _Gateway(concept["label"]["de"])
    service = _service(gateway, monkeypatch)

    _, value, error = await service.extract_field(field, "Text", language="de")

    assert error is None
    assert value == concept["uri"]
    assert gateway.calls == 1


async def test_a_uri_skos_field_keeps_no_invalid_label_after_the_last_try(
    monkeypatch,
):
    field = _field("didactic_planning_tools.json", "dpt:concept")
    gateway = _Gateway("Völlig unbekannter Begriff ohne Konzept")
    service = _service(gateway, monkeypatch)

    _, value, error = await service.extract_field(field, "Text", language="de")

    assert error is None
    assert value is None
    assert gateway.calls == 1 + service.settings.llm_max_retries


# ------------------------------------------------------------ #14 blank answer


@pytest.mark.parametrize("blank", ["", "   "])
async def test_a_blank_answer_for_an_optional_closed_field_is_not_retried(
    monkeypatch, blank
):
    field = _field("core.json", "ccm:educationalcontext")
    assert not field["system"].get("required")
    gateway = _Gateway(blank)
    service = _service(gateway, monkeypatch)

    _, value, error = await service.extract_field(field, "Text", language="de")

    assert (value, error) == (None, None)
    assert gateway.calls == 1


# ------------------------------------------------------------ #16 non-JSON 200


async def test_a_200_without_json_is_an_upstream_error_and_retried(monkeypatch):
    field = _field("core.json", "cclom:title")
    gateway = _Gateway(html=True)
    service = _service(gateway, monkeypatch)

    _, value, error = await service.extract_field(field, "Text", language="de")

    assert value is None
    assert isinstance(error, LLMUpstreamError)
    assert gateway.calls == service.max_retries


async def test_a_200_with_a_json_list_is_an_upstream_error(monkeypatch):
    def handler(request):
        return httpx.Response(200, json=["not", "an", "answer"])

    _settings(monkeypatch)
    service = LLMService(llm_provider="b-api-openai")
    service.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    _, value, error = await service.extract_field(
        _field("core.json", "cclom:title"), "Text", language="de"
    )

    assert value is None
    assert isinstance(error, LLMUpstreamError)


# ------------------------------------------------------------ tracing headers


def test_native_openai_gets_no_tracing_headers(monkeypatch):
    _settings(monkeypatch, openai_api_key="test-key")

    service = LLMService(llm_provider="openai")

    assert propagate_trace not in service.http_client.event_hooks["request"]


def test_the_b_api_still_gets_tracing_headers(monkeypatch):
    _settings(monkeypatch)

    service = LLMService(llm_provider="b-api-openai")

    assert propagate_trace in service.http_client.event_hooks["request"]
