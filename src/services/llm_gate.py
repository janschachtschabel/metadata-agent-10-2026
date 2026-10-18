"""The concurrency and rate budget of each LLM gateway, shared by every LLMService."""

import asyncio
from typing import Any, Optional
from weakref import WeakKeyDictionary


class RateLimiter:
    """
    Hands out evenly spaced send slots.

    AcademicCloud answers 429 without a `retry-after`, so there is nothing to read a
    wait off — a client has to stay under the limit rather than react to it. Its
    bucket is roughly four deep and refills at the allowed rate, so a burst is
    what empties it; spacing the starts is what keeps it full.

    A slot that already passed is not a debt: a client idle for a minute starts
    immediately instead of earning credit for the pause.
    """

    def __init__(self, requests_per_second: float):
        self.requests_per_second = float(requests_per_second or 0.0)
        self._interval = (
            1.0 / self.requests_per_second if self.requests_per_second > 0 else 0.0
        )
        self._lock = asyncio.Lock()
        self._next_slot: Optional[float] = None

    async def acquire(self) -> float:
        """Wait for this caller's slot. Returns how long it waited, in seconds."""
        if not self._interval:
            return 0.0

        loop = asyncio.get_running_loop()
        # The slot is claimed under the lock but waited for outside it, so the
        # queue is handed out at once instead of each caller blocking the next.
        async with self._lock:
            now = loop.time()
            slot = now if self._next_slot is None else max(now, self._next_slot)
            self._next_slot = slot + self._interval

        delay = slot - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        return max(delay, 0.0)


class ProviderGate:
    """The concurrency and rate budget of one gateway, shared by everything using it."""

    def __init__(self, max_concurrent_requests: int, requests_per_second: float):
        self.max_concurrent_requests = max_concurrent_requests
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        self.rate_limiter = RateLimiter(requests_per_second)

    @property
    def requests_per_second(self) -> float:
        return self.rate_limiter.requests_per_second


# The budget belongs to the provider's limit group, not to a service object:
# get_llm_service() builds a fresh LLMService whenever a request overrides the
# provider or model, so a per-instance semaphore would let two concurrent API
# requests send twice the allowed number. Keyed by event loop as well because a
# semaphore binds to the loop it is first awaited in — one loop in production,
# one per test.
_gates: "WeakKeyDictionary[Any, dict[str, ProviderGate]]" = WeakKeyDictionary()


def get_provider_gate(
    limit_group: str, max_concurrent_requests: int, requests_per_second: float
) -> ProviderGate:
    """Get (or build) the shared gate for a limit group."""
    per_loop = _gates.setdefault(asyncio.get_running_loop(), {})
    gate = per_loop.get(limit_group)

    if (
        gate is None
        or gate.max_concurrent_requests != max_concurrent_requests
        or gate.requests_per_second != float(requests_per_second or 0.0)
    ):
        gate = ProviderGate(max_concurrent_requests, requests_per_second)
        per_loop[limit_group] = gate

    return gate


def reset_provider_gates() -> None:
    """Drop all gates. For tests — production configures them once at startup."""
    _gates.clear()
