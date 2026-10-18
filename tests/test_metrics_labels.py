"""
Label sets of the agent's metrics that the alerts and the scrape depend on.

- OPS-06: a labelled counter series only exists after its first .inc(), and
  increase() does not count that first value. A key refusal right after a pod
  start created its series at 1 and the critical alert never fired. The
  internal app therefore creates the series the critical rules watch, at 0,
  when it starts.
- OPS-14: the method label comes from the client. On an unmatched path any
  token became a label value - unbounded cardinality.
"""

import re
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from src.config import Settings
from src.infra import create_internal_app
from src.metrics import CRITICAL_LLM_RESULTS, HttpMetricsMiddleware
from src.services import llm_errors

ALERTS = (
    Path(__file__).resolve().parents[1]
    / "helm"
    / "metadata-agent"
    / "files"
    / "alerts.yaml"
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{"b_api_key": "own-key", **overrides})


def _llm_sample(**labels):
    return REGISTRY.get_sample_value("metadata_agent_llm_requests_total", labels)


def _error_codes() -> set[str]:
    return {
        cls.code
        for cls in vars(llm_errors).values()
        if isinstance(cls, type) and issubclass(cls, llm_errors.LLMError)
    }


def _critical_result_patterns() -> list[str]:
    groups = yaml.safe_load(ALERTS.read_text("utf-8"))
    patterns = []
    for group in groups["groups"]:
        for rule in group["rules"]:
            if (
                rule["labels"]["severity"] == "critical"
                and "llm_requests" in (rule["expr"])
            ):
                patterns += re.findall(r'result=~?"([^"]+)"', rule["expr"])
    return patterns


# ------------------------------------------------------------ OPS-06


def test_the_precreated_results_are_exactly_what_the_critical_rules_watch():
    watched = {
        code
        for code in _error_codes()
        for pattern in _critical_result_patterns()
        if re.fullmatch(pattern, code)
    }

    assert watched, "no critical LLM rule found"
    assert set(CRITICAL_LLM_RESULTS) == watched


@pytest.mark.parametrize(
    "overrides, provider, sources",
    [
        ({}, "b-api-openai", {"static", "none", "forwarded"}),
        ({"llm_provider": "b-api-router"}, "b-api-router", {"static", "none"}),
        (
            {"llm_provider": "openai", "openai_api_key": "k"},
            "openai",
            {"openai"},
        ),
    ],
)
def test_the_internal_app_creates_the_critical_series_at_zero(
    overrides, provider, sources
):
    internal = create_internal_app(lambda: _settings(**overrides))

    with TestClient(internal) as client:
        assert client.get("/metrics").status_code == 200

    for result in CRITICAL_LLM_RESULTS:
        for source in sources:
            labels = dict(
                provider=provider, result=result, auth_source=source, config="default"
            )
            assert _llm_sample(**labels) is not None, labels
    # Overrides are the caller's problem and never page: not created.
    assert (
        _llm_sample(
            provider=provider,
            result=CRITICAL_LLM_RESULTS[0],
            auth_source=sorted(sources)[0],
            config="override",
        )
        is None
    )


# ------------------------------------------------------------ OPS-14


def _app_with_metrics() -> FastAPI:
    app = FastAPI()
    app.add_middleware(HttpMetricsMiddleware)
    return app


def _http_sample(method: str) -> float | None:
    return REGISTRY.get_sample_value(
        "metadata_agent_http_requests_total",
        {"method": method, "route": "unmatched", "status": "404"},
    )


def test_an_unknown_method_is_counted_as_other():
    before = _http_sample("OTHER") or 0.0

    with TestClient(_app_with_metrics()) as client:
        client.request("BREW", "/no-such-path")

    assert _http_sample("BREW") is None
    assert _http_sample("OTHER") == before + 1


def test_standard_methods_keep_their_name():
    before = _http_sample("DELETE") or 0.0

    with TestClient(_app_with_metrics()) as client:
        client.delete("/no-such-path")

    assert _http_sample("DELETE") == before + 1
