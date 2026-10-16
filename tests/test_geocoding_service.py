"""
Geocoding of schema:location through Photon, against a stand-in Photon.

Photon reports `countrycode` only for some places, so the enrichment has to
cope with it missing — one location without it used to raise and cost the
enrichment of every location. The 1-request-per-second limit Photon asks for
has to hold for concurrent requests too, not only for consecutive ones.
"""

import asyncio
import time

import httpx
import pytest

from src.services import geocoding_service as geo_module
from src.services.geocoding_service import GeocodingService


def _feature(countrycode="de", city="Weimar"):
    properties = {"city": city, "state": "Thüringen", "country": "Deutschland"}
    if countrycode is not None:
        properties["countrycode"] = countrycode
    return {
        "features": [
            {
                "geometry": {"coordinates": [11.3297921, 50.9793301]},
                "properties": properties,
            }
        ]
    }


@pytest.fixture
def photon(monkeypatch):
    """Route the service's HTTP client to a handler the test controls."""
    state = {"answer": _feature(), "times": []}

    def handler(request):
        state["times"].append(time.monotonic())
        return httpx.Response(200, json=state["answer"])

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(geo_module.httpx, "AsyncClient", client)
    return state


@pytest.mark.asyncio
async def test_a_place_string_becomes_a_structured_location_with_coordinates(photon):
    metadata = {"schema:location": ["Weimar"]}

    result = await GeocodingService().enrich_metadata_with_geocoding(metadata)

    (location,) = result["schema:location"]
    assert location["geo"] == {"latitude": 50.9793301, "longitude": 11.3297921}
    assert location["address"]["addressLocality"] == "Weimar"
    assert location["address"]["addressCountry"] == "DE"


@pytest.mark.asyncio
async def test_a_place_without_country_code_keeps_its_coordinates(photon):
    photon["answer"] = _feature(countrycode=None)
    metadata = {"schema:location": ["Weimar"]}

    result = await GeocodingService().enrich_metadata_with_geocoding(metadata)

    (location,) = result["schema:location"]
    assert location["geo"]["latitude"] == 50.9793301
    assert location["address"]["addressCountry"] == "Deutschland"


@pytest.mark.asyncio
async def test_an_object_without_address_is_filled_even_without_country_code(photon):
    photon["answer"] = _feature(countrycode=None)
    metadata = {"schema:location": [{"name": "Weimar", "address": {}}]}

    result = await GeocodingService().enrich_metadata_with_geocoding(metadata)

    (location,) = result["schema:location"]
    assert location["geo"]["longitude"] == 11.3297921
    assert location["address"]["addressCountry"] == ""


@pytest.mark.asyncio
async def test_a_location_with_coordinates_is_not_looked_up_again(photon):
    known = {"name": "DHM", "geo": {"latitude": 52.5, "longitude": 13.4}}

    result = await GeocodingService().enrich_metadata_with_geocoding(
        {"schema:location": [known]}
    )

    assert result["schema:location"] == [known]
    assert photon["times"] == []


@pytest.mark.asyncio
async def test_concurrent_lookups_still_keep_the_rate_limit(photon, monkeypatch):
    monkeypatch.setattr(GeocodingService, "RATE_LIMIT_MS", 80)
    service = GeocodingService()

    # The second and third both have to wait; each must wait for its own slot.
    await asyncio.gather(
        service.geocode_address("Weimar"),
        service.geocode_address("Erfurt"),
        service.geocode_address("Jena"),
    )

    times = sorted(photon["times"])
    assert all(later - earlier >= 0.07 for earlier, later in zip(times, times[1:]))
