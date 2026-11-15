"""
Work nobody waits for is stopped, and a pod takes on only what it can finish.

Audit 13.11., P-10: uvicorn does not cancel a handler when the client closes
the connection. A proxy that gave up after 60-120 s left a /generate running
for up to 300 s - some 50 LLM calls for nobody - and a second try ran next
to it. P-12: all extractions of a pod share one gate of 20 calls in flight;
from about 15 at once they all reached the deadline together.
"""

import asyncio

import httpx
import pytest

from src import main
from src.api import deps
from src.api.disconnect import ClientDisconnected, run_unless_disconnected
from src.api.routers import generate as generate_module


class _Request:
    """ASGI receive after the body was read: nothing until the client leaves."""

    def __init__(self, gone_after: float | None = None):
        self.gone_after = gone_after

    async def receive(self) -> dict:
        if self.gone_after is None:
            await asyncio.Event().wait()
        await asyncio.sleep(self.gone_after)
        return {"type": "http.disconnect"}


class _Work:
    def __init__(self, seconds: float):
        self.seconds, self.cancelled = seconds, False

    async def run(self):
        try:
            await asyncio.sleep(self.seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return "done"


@pytest.mark.asyncio
async def test_work_that_finishes_gives_its_result():
    assert await run_unless_disconnected(_Request(), _Work(0.02).run()) == "done"


@pytest.mark.asyncio
async def test_work_is_stopped_when_the_client_is_gone():
    work = _Work(30)

    with pytest.raises(ClientDisconnected):
        await run_unless_disconnected(_Request(gone_after=0.05), work.run())

    assert work.cancelled


@pytest.mark.asyncio
async def test_a_fault_in_the_work_is_raised_as_it_is():
    async def broken():
        raise ValueError("kaputt")

    with pytest.raises(ValueError, match="kaputt"):
        await run_unless_disconnected(_Request(), broken())


@pytest.mark.asyncio
async def test_a_deadline_around_it_stops_the_work_too():
    work = _Work(30)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await run_unless_disconnected(_Request(), work.run())

    assert work.cancelled


# ------------------------------------------------------------ admission


class _HeldService:
    """generate_metadata waits until released."""

    llm_service = None

    def __init__(self):
        self.release = asyncio.Event()
        self.started = 0

    async def generate_metadata(self, **kwargs):
        self.started += 1
        await self.release.wait()
        return {
            "contextName": "default",
            "schemaVersion": "2.0.0",
            "metadataset": "event.json",
            "language": "de",
            "exportedAt": "2026-11-13T00:00:00Z",
            "metadata": {"cclom:title": "Kurs"},
            "processing": {"status": "success", "success": True, "warnings": []},
        }


async def _post(client):
    return await client.post("/generate", json={"text": "Ein Text"})


@pytest.mark.asyncio
async def test_above_the_limit_a_generation_is_turned_away_at_once(monkeypatch):
    service = _HeldService()
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: service)
    monkeypatch.setattr(deps.settings, "max_concurrent_generations", 1)
    transport = httpx.ASGITransport(app=main.app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.ensure_future(_post(client))
        while service.started == 0:
            await asyncio.sleep(0.01)

        second = await _post(client)

        service.release.set()
        first = await first

    assert first.status_code == 200
    assert second.status_code == 503
    assert second.json()["error"]["code"] == "TOO_MANY_GENERATIONS"
    assert int(second.headers["retry-after"]) > 0
    assert service.started == 1
    # The slot is free again.
    assert generate_module._running_generations == 0


@pytest.mark.asyncio
async def test_without_a_limit_nothing_is_turned_away(monkeypatch):
    service = _HeldService()
    service.release.set()
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: service)
    transport = httpx.ASGITransport(app=main.app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        answers = await asyncio.gather(*(_post(client) for _ in range(5)))

    assert [a.status_code for a in answers] == [200] * 5
    assert deps.settings.max_concurrent_generations is None
