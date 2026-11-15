"""
Geocoding gets what is left of the /generate deadline, not more (review #11).

Photon allows one lookup per second, shared by the whole process. Waiting for
a slot counted against GENERATE_TIMEOUT, and with a longer queue than time
left the whole run ended in 504 GENERATION_TIMEOUT - every extracted field
lost for a few coordinates. Now a lookup whose slot falls after the budget is
not made, one in flight is cut at the budget, the locations stay as they are
and a warning says so.
"""

import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services import geocoding_service as geo_module
from src.services.geocoding_service import GEOCODING_RESERVE_SECONDS, GeocodingService
from src.services.metadata_service import MetadataService

WEIMAR = {
    "features": [
        {
            "geometry": {"coordinates": [11.3297921, 50.9793301]},
            "properties": {"city": "Weimar", "countrycode": "de"},
        }
    ]
}


@pytest.fixture
def photon(monkeypatch):
    """The service's HTTP client answers from here; `delay` holds the answer back."""
    state = {"calls": 0, "delay": 0.0}

    async def handler(request):
        state["calls"] += 1
        await asyncio.sleep(state["delay"])
        return httpx.Response(200, json=WEIMAR)

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(geo_module.httpx, "AsyncClient", client)
    return state


def _deadline(seconds_of_lookups):
    """A deadline leaving `seconds_of_lookups` for geocoding after the reserve."""
    loop = asyncio.get_running_loop()
    return loop.time() + GEOCODING_RESERVE_SECONDS + seconds_of_lookups


async def test_a_queue_longer_than_the_budget_costs_no_wait(photon):
    service = GeocodingService()
    # Other requests hold the next minute of Photon slots.
    service._next_slot = time.monotonic() * 1000 + 60_000
    queued_until = service._next_slot
    warnings: list[str] = []

    result = await asyncio.wait_for(
        service.enrich_metadata_with_geocoding(
            {"schema:location": ["Weimar"]}, deadline=_deadline(2), warnings=warnings
        ),
        timeout=3,
    )

    assert result["schema:location"] == ["Weimar"]
    assert photon["calls"] == 0
    assert service._next_slot == queued_until  # no slot reserved for nothing
    assert len(warnings) == 1 and "1" in warnings[0]


async def test_lookups_before_the_budget_end_keep_their_coordinates(photon):
    service = GeocodingService()
    warnings: list[str] = []

    # The first slot is now, the second one second later - after the budget.
    result = await service.enrich_metadata_with_geocoding(
        {"schema:location": ["Weimar", "Erfurt"]},
        deadline=_deadline(0.5),
        warnings=warnings,
    )

    first, second = result["schema:location"]
    assert first["geo"]["latitude"] == 50.9793301
    assert second == "Erfurt"
    assert photon["calls"] == 1
    assert len(warnings) == 1


async def test_a_lookup_still_running_at_the_budget_end_is_cut(photon):
    photon["delay"] = 5
    service = GeocodingService()
    warnings: list[str] = []

    result = await asyncio.wait_for(
        service.enrich_metadata_with_geocoding(
            {"schema:location": ["Weimar"]},
            deadline=_deadline(0.3),
            warnings=warnings,
        ),
        timeout=2,
    )

    assert result["schema:location"] == ["Weimar"]
    assert len(warnings) == 1


async def test_without_a_deadline_nothing_changes(photon):
    warnings: list[str] = []

    result = await GeocodingService().enrich_metadata_with_geocoding(
        {"schema:location": ["Weimar"]}, warnings=warnings
    )

    assert result["schema:location"][0]["geo"]["latitude"] == 50.9793301
    assert warnings == []


async def test_post_processing_hands_the_deadline_to_geocoding():
    seen = {}

    async def enrich(metadata, language, deadline=None, warnings=None):
        seen["deadline"] = deadline
        return metadata

    service = MetadataService.__new__(MetadataService)
    service.geocoding_service = type("G", (), {})()
    service.geocoding_service.enrich_metadata_with_geocoding = enrich

    await service._post_process(
        {"schema:location": ["Weimar"]}, {}, "de", False, False, True, [], 123.0
    )

    assert seen["deadline"] == 123.0


def test_the_generate_route_hands_its_deadline_to_the_service(monkeypatch):
    seen = {}

    class _Service:
        llm_service = None

        async def generate_metadata(self, **kwargs):
            seen.update(kwargs)
            return {
                "contextName": "default",
                "schemaVersion": "2.0.0",
                "metadataset": "event.json",
                "language": "de",
                "exportedAt": "2026-10-18T00:00:00Z",
                "metadata": {"cclom:title": "Kurs"},
                "processing": {"status": "success", "success": True, "warnings": []},
            }

    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: _Service())

    with TestClient(main.app) as client:
        response = client.post("/generate", json={"text": "Ein Kurs."})

    assert response.status_code == 200, response.text
    assert isinstance(seen["options"].deadline, float)
