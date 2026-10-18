"""
Parallelism: provider defaults, shared ENV overrides and the /generate worker default.

The OpenAI paths may run 20 requests at once, AcademicCloud keeps its measured
2/2. A request without max_workers takes METADATA_AGENT_DEFAULT_MAX_WORKERS —
before, a fixed request default of 10 silently ignored that setting.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.config import Settings
from src.services import llm_service as llm_module
from src.services.llm_service import LLMService, reset_provider_gates


@pytest.mark.parametrize(
    "provider, expected",
    [("b-api-openai", 20), ("openai", 20), ("b-api-academiccloud", 2)],
)
@pytest.mark.parametrize("unset", [None, "", "  "])
def test_unset_override_uses_the_provider_default(provider, expected, unset):
    settings = Settings(
        _env_file=None,
        llm_max_concurrent_requests=unset,
        llm_max_requests_per_second=unset,
    )

    assert (
        settings.get_throughput_limits(provider)["max_concurrent_requests"] == expected
    )


def test_twenty_workers_are_the_default():
    assert Settings.model_fields["default_max_workers"].default == 20


@pytest.mark.parametrize("configured, effective", [("50", 20), ("0", 1), ("-3", 1)])
def test_an_out_of_range_worker_default_is_clamped_to_the_request_range(
    monkeypatch, configured, effective
):
    """Requests accept 1-20; the startup log and /generate show what applies."""
    monkeypatch.setenv("METADATA_AGENT_DEFAULT_MAX_WORKERS", configured)

    assert Settings(_env_file=None).default_max_workers == effective


@pytest.mark.parametrize("provider", ["b-api-openai", "openai", "b-api-academiccloud"])
@pytest.mark.parametrize("concurrent", [1, 40])
def test_the_shared_env_override_wins_for_every_provider(
    monkeypatch, provider, concurrent
):
    monkeypatch.setenv("METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS", str(concurrent))
    monkeypatch.setenv("METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND", "7.5")

    limits = Settings(_env_file=None).get_throughput_limits(provider)

    assert limits["max_concurrent_requests"] == concurrent
    assert limits["requests_per_second"] == 7.5


async def test_the_openai_path_runs_twenty_calls_in_parallel(monkeypatch):
    for name in (
        "METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS",
        "METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND",
    ):
        monkeypatch.delenv(name, raising=False)
    # A developer's .env must not decide this test.
    monkeypatch.setattr(llm_module, "get_settings", lambda: Settings(_env_file=None))
    reset_provider_gates()
    service = LLMService(llm_provider="b-api-openai")
    active = peak = 0
    twenty_started = asyncio.Event()
    release = asyncio.Event()

    async def post(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 20:
            twenty_started.set()
        try:
            await release.wait()
            return httpx.Response(200, json={"choices": []})
        finally:
            active -= 1

    monkeypatch.setattr(service.http_client, "post", AsyncMock(side_effect=post))
    tasks = [
        asyncio.create_task(service._call_llm([{"role": "user", "content": "x"}]))
        for _ in range(24)
    ]
    try:
        async with asyncio.timeout(2):
            await twenty_started.wait()
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.close()
        reset_provider_gates()

    assert peak == 20


@pytest.mark.parametrize(
    "mode, configured, explicit, expected",
    [
        ("json", 20, None, 20),
        ("json", 20, 7, 7),
        ("plain", 20, None, 20),
        ("json", 12, None, 12),
        # The request model accepts 1..20; an ENV value outside must not turn
        # every request without max_workers into a 422.
        ("json", 50, None, 20),
        ("plain", 0, None, 1),
    ],
)
def test_generate_uses_the_env_worker_default_unless_the_request_overrides(
    monkeypatch, mode, configured, explicit, expected
):
    monkeypatch.setattr(
        deps, "settings", Settings(_env_file=None, default_max_workers=configured)
    )
    generate = AsyncMock(
        return_value={
            "contextName": "default",
            "schemaVersion": "1.8.1",
            "language": "de",
            "exportedAt": "2026-10-07T00:00:00Z",
            "metadataset": "core.json",
            "processing": {"success": True},
        }
    )
    service = SimpleNamespace(
        generate_metadata=generate,
        llm_service=SimpleNamespace(close=AsyncMock()),
    )
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kwargs: service)

    body = {"text": "A resource", "schema_file": "core.json"}
    if explicit is not None:
        body["max_workers"] = explicit
    with TestClient(main.app) as client:
        if mode == "plain":
            response = client.post(
                "/generate",
                content="A resource",
                headers={"Content-Type": "text/plain"},
            )
        else:
            response = client.post("/generate", json=body)

    assert response.status_code == 200, response.text
    assert generate.await_args.kwargs["max_workers"] == expected
