"""
When the agent last failed, as a gauge the critical alerts read (audit
14.11.2026, O-34).

The critical rules page on the first failure. increase() does not count a
series' first value, so they also counted "new" series - series without a
sample in a look-back window. A scrape gap longer than that window made an old
counter look new and paged for an old failure, whatever the window. The agent
now says itself when it last failed:

- metadata_agent_llm_last_failure_timestamp_seconds, with the labels of
  metadata_agent_llm_requests_total (the rules select the operator's own key
  and the configured model by them), set with every call that ends in an error
- metadata_agent_http_last_internal_error_timestamp_seconds by route, set
  with every answer of status 500

and the rules read `time() - <gauge> < 600`.
"""

import re
import time
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from src.config import Settings
from src.metrics import HttpMetricsMiddleware
from src.services.llm_auth import forwarded_authorization
from src.services.llm_errors import LLMError
from src.services.llm_service import LLMService, reset_provider_gates
from conftest import use_settings

LLM_GAUGE = "metadata_agent_llm_last_failure_timestamp_seconds"
HTTP_GAUGE = "metadata_agent_http_last_internal_error_timestamp_seconds"
ALERTS = (
    Path(__file__).resolve().parents[1]
    / "helm"
    / "metadata-agent"
    / "files"
    / "alerts.yaml"
)
MESSAGES = [{"role": "user", "content": "x"}]
OK = {"choices": [{"message": {"content": '{"value": "ok"}'}}]}


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


def _service(monkeypatch, response, provider="b-api-openai", model=None):
    cfg = Settings(_env_file=None, b_api_key="own-key", llm_retry_delay=0)
    use_settings(monkeypatch, cfg)
    service = LLMService(llm_provider=provider, llm_model=model)
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response)
    )
    return service


def _last_llm_failure(result, auth_source="static", config="default"):
    labels = {
        "provider": "b-api-openai",
        "result": result,
        "auth_source": auth_source,
        "config": config,
    }
    return REGISTRY.get_sample_value(LLM_GAUGE, labels)


# ------------------------------------------------------------ LLM calls


async def test_a_failed_call_notes_when_it_failed(monkeypatch):
    service = _service(monkeypatch, httpx.Response(401, text="Unauthorized"))
    before = time.time()

    with pytest.raises(LLMError):
        await service._call_llm(MESSAGES)

    assert before <= _last_llm_failure("LLM_AUTH_REJECTED") <= time.time()


async def test_the_time_carries_the_labels_the_rules_select_by(monkeypatch):
    """A caller's key and a model the request picked never page."""
    service = _service(monkeypatch, httpx.Response(403, text="Forbidden"))
    picked = _service(
        monkeypatch, httpx.Response(404, text="no such model"), model="gibt-es-nicht"
    )
    own_key = _last_llm_failure("LLM_AUTH_FORBIDDEN")
    own_model = _last_llm_failure("LLM_MODEL_NOT_FOUND")

    with forwarded_authorization("Bearer caller-key"):
        with pytest.raises(LLMError):
            await service._call_llm(MESSAGES)
    with pytest.raises(LLMError):
        await picked._call_llm(MESSAGES)

    assert _last_llm_failure("LLM_AUTH_FORBIDDEN", auth_source="forwarded")
    assert _last_llm_failure("LLM_MODEL_NOT_FOUND", config="override")
    assert _last_llm_failure("LLM_AUTH_FORBIDDEN") == own_key
    assert _last_llm_failure("LLM_MODEL_NOT_FOUND") == own_model


async def test_an_answered_call_is_no_failure(monkeypatch):
    service = _service(monkeypatch, httpx.Response(200, json=OK))

    await service._call_llm(MESSAGES)

    assert _last_llm_failure("ok") is None


# ------------------------------------------------------------ status 500


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(HttpMetricsMiddleware)

    @app.get("/kaputt-{code}")
    async def broken(code: int):
        raise HTTPException(status_code=code, detail="x")

    @app.get("/ausnahme")
    async def raising():
        raise RuntimeError("boom")

    return app


def _last_internal_error(route):
    return REGISTRY.get_sample_value(HTTP_GAUGE, {"route": route})


def test_an_answer_of_status_500_notes_when_it_was_given():
    before = time.time()

    with TestClient(_app(), raise_server_exceptions=False) as client:
        assert client.get("/ausnahme").status_code == 500

    assert before <= _last_internal_error("/ausnahme") <= time.time()


def test_other_statuses_are_no_internal_error():
    with TestClient(_app(), raise_server_exceptions=False) as client:
        for code in (404, 502, 503):
            assert client.get(f"/kaputt-{code}").status_code == code

    assert _last_internal_error("/kaputt-{code}") is None


# ------------------------------------------------------------ the rules


def _critical_rules() -> list[dict]:
    groups = yaml.safe_load(ALERTS.read_text("utf-8"))["groups"]
    rules = [rule for group in groups for rule in group["rules"]]
    return [rule for rule in rules if rule["labels"]["severity"] == "critical"]


def test_every_critical_rule_reads_the_time_of_the_last_failure():
    rules = _critical_rules()

    assert {rule["alert"] for rule in rules} == {
        "MetadataAgentLlmKeyRefused",
        "MetadataAgentLlmRouteMisconfigured",
        "MetadataAgentInternalError",
    }
    for rule in rules:
        gauge = LLM_GAUGE if "llm_requests" in rule["expr"] else HTTP_GAUGE
        assert re.search(
            r"time\(\)\s*-\s*max by \([a-z, ]+\) \("
            + gauge
            + r"(\{[^}]*\})?\)\s*<\s*600",
            rule["expr"],
        ), rule["alert"]
        # No look-back that a longer scrape gap outlasts.
        assert "last_over_time" not in rule["expr"], rule["alert"]
        assert "unless" not in rule["expr"], rule["alert"]


def test_the_time_is_selected_like_the_counter_beside_it():
    """The same result, key source and config: or a caller's failure pages."""
    for rule in _critical_rules():
        if "llm_requests" not in rule["expr"]:
            continue
        counted = set(
            re.findall(r"metadata_agent_llm_requests_total(\{[^}]*\})", rule["expr"])
        )
        timed = set(re.findall(LLM_GAUGE + r"(\{[^}]*\})", rule["expr"]))

        assert len(counted) == 1 and timed == counted, rule["alert"]
