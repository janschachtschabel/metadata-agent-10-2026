"""
Performance findings of the audit of 2026-10-18 (P-02, P-03, P-05).

- P-05: fuzzy vocabulary matching computed the full edit distance to every
  concept label on the event loop (220 labels in ccm:oeh_lrt); measured 1.9 to
  4.5 s per /generate. Only distances up to a small limit matter.
- P-03: the geocoding throttle held its lock while sleeping; one cancelled or
  slow waiter held up every other request in the process.
- P-02: the cache primer skipped b-api-router although its default model is an
  OpenAI model (openai/gpt-6-luna) with the same prompt cache.
"""

import asyncio
import random

import pytest

from src.services import geocoding_service as geocoding_module
from src.services.geocoding_service import GeocodingService
from src.services.llm_service import LLMService
from src.utils.text_utils import levenshtein_distance, levenshtein_within

# ------------------------------------------------------------------- P-05

WORDS = [
    "Arbeitsblatt",
    "Arbeitsblätter",
    "Video",
    "Vidoe",
    "",
    "x",
    "Unterrichtsplanung",
]


@pytest.mark.parametrize("a", WORDS)
@pytest.mark.parametrize("b", WORDS)
@pytest.mark.parametrize("limit", [0, 1, 2, 3])
def test_the_bounded_distance_agrees_with_the_full_one(a, b, limit):
    full = levenshtein_distance(a, b)

    assert levenshtein_within(a, b, limit) == (full if full <= limit else None)


def test_the_bounded_distance_agrees_on_random_strings():
    rng = random.Random(7)
    for _ in range(500):
        a = "".join(rng.choice("abcde") for _ in range(rng.randint(0, 12)))
        b = "".join(rng.choice("abcde") for _ in range(rng.randint(0, 12)))
        limit = rng.randint(0, 4)
        full = levenshtein_distance(a, b)
        assert levenshtein_within(a, b, limit) == (full if full <= limit else None)


def test_a_long_label_far_off_is_rejected_without_the_full_table(monkeypatch):
    calls = {"rows": 0}
    real = levenshtein_distance

    def counting(a, b):
        calls["rows"] += 1
        return real(a, b)

    monkeypatch.setattr("src.utils.text_utils.levenshtein_distance", counting)

    assert levenshtein_within("video", "x" * 400, 3) is None
    assert calls["rows"] == 0, "rejected by length alone"


# ------------------------------------------------------------------- P-03


async def test_geocoding_waiters_get_one_slot_each_without_holding_a_lock(monkeypatch):
    clock = {"now": 100.0}
    slept = []

    async def fake_sleep(seconds):
        slept.append(round(seconds, 3))

    monkeypatch.setattr(geocoding_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(geocoding_module.asyncio, "sleep", fake_sleep)
    service = GeocodingService()

    await asyncio.gather(*(service._wait_for_rate_limit() for _ in range(3)))

    assert sorted(slept) == [0.0, 1.0, 2.0]
    assert not hasattr(service, "_rate_lock")


async def test_a_cancelled_geocoding_waiter_does_not_block_the_next(monkeypatch):
    service = GeocodingService()
    service.RATE_LIMIT_MS = 50
    first = asyncio.create_task(service._wait_for_rate_limit())
    await asyncio.sleep(0)
    second = asyncio.create_task(service._wait_for_rate_limit())
    await asyncio.sleep(0)
    second.cancel()

    third = asyncio.create_task(service._wait_for_rate_limit())
    await asyncio.wait_for(asyncio.gather(first, third), timeout=1)


# ------------------------------------------------------------------- P-02


@pytest.mark.parametrize(
    "provider, model, primes",
    [
        ("b-api-openai", "gpt-6-luna", True),
        ("openai", "gpt-6-luna", True),
        ("b-api-router", "openai/gpt-6-luna", True),
        ("b-api-router", "wlo-academiccloud-route", False),
        ("b-api-academiccloud", "qwen3.6-35b-a3b", False),
    ],
)
def test_the_cache_primer_runs_for_openai_models_only(provider, model, primes):
    service = LLMService.__new__(LLMService)
    service.provider, service.model = provider, model

    assert service._primes_prompt_cache() is primes
