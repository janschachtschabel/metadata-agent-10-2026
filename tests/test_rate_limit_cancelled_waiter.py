"""
A cancelled waiter gives its send slot back (audit 14.11.2026, L-N).

Both rate limiters - the one of an LLM gateway and the one for Photon - hand
out a slot first and let the caller sleep until it comes. A caller cancelled
while it slept (the deadline of a run, a caller that went away) kept the slot:
everybody after it waited one interval longer for a request that was never
sent. At the deadline of a large extraction that was one interval per field.
"""

import asyncio
import time

import pytest

from src.services.geocoding_service import GeocodingService
from src.services.llm_gate import RateLimiter


async def _cancel(task):
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_the_next_llm_call_takes_the_slot_of_a_cancelled_one():
    limiter = RateLimiter(2.0)  # slots 0.5 s apart
    await limiter.acquire()

    await _cancel(asyncio.ensure_future(limiter.acquire()))
    waited = await limiter.acquire()

    # The cancelled caller's slot at 0.5 s, not the one after it at 1.0 s.
    assert 0.3 < waited < 0.75


async def test_several_cancelled_llm_calls_leave_no_gap():
    limiter = RateLimiter(10.0)  # slots 0.1 s apart
    await limiter.acquire()
    waiters = [asyncio.ensure_future(limiter.acquire()) for _ in range(20)]
    await asyncio.sleep(0.01)
    for waiter in waiters:
        waiter.cancel()
    await asyncio.gather(*waiters, return_exceptions=True)

    first = await limiter.acquire()
    second = await limiter.acquire()

    assert first < 0.3
    assert second < 0.3


async def test_llm_slots_stay_one_interval_apart():
    limiter = RateLimiter(10.0)
    await limiter.acquire()
    cancelled = asyncio.ensure_future(limiter.acquire())
    kept = asyncio.ensure_future(limiter.acquire())
    await _cancel(cancelled)
    loop = asyncio.get_running_loop()
    start = loop.time()

    await asyncio.gather(kept, limiter.acquire(), limiter.acquire())

    # Slots at 0.1 (returned), 0.2 (kept) and 0.3: three calls end after 0.3 s.
    assert 0.2 < loop.time() - start < 0.45


async def test_a_slot_that_has_passed_is_no_credit():
    limiter = RateLimiter(10.0)
    await limiter.acquire()
    await _cancel(asyncio.ensure_future(limiter.acquire()))
    await asyncio.sleep(0.3)

    assert await limiter.acquire() == 0.0
    # The next one is spaced from that call, not from the expired slot.
    assert await limiter.acquire() > 0.05


async def test_the_next_lookup_takes_the_slot_of_a_cancelled_one():
    service = GeocodingService()
    service.RATE_LIMIT_MS = 400
    await service._wait_for_rate_limit()

    await _cancel(asyncio.ensure_future(service._wait_for_rate_limit()))
    start = time.monotonic()
    await service._wait_for_rate_limit()

    # The cancelled lookup's slot at 0.4 s, not the one after it at 0.8 s.
    assert 0.2 < time.monotonic() - start < 0.6
