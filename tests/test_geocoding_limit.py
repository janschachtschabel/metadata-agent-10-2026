"""
Geocoding is bounded per request (audit P-03).

Photon allows one lookup per second, and the schedule is shared by the whole
process. schema:location comes from the LLM and from the caller's
existing_metadata, unfiltered: a request with 200 places held every other
request's geocoding for over three minutes.
"""

import pytest

from src.services.geocoding_service import MAX_GEOCODED_LOCATIONS, GeocodingService


@pytest.mark.asyncio
async def test_only_the_first_locations_are_looked_up():
    service = GeocodingService()
    lookups = []

    async def geocode(address, language="de"):
        lookups.append(address)
        return None

    service.geocode_address = geocode
    places = [f"Ort {i}, 0{i:04d} Stadt" for i in range(15)]

    result = await service.enrich_metadata_with_geocoding(
        {"schema:location": list(places)}
    )

    assert MAX_GEOCODED_LOCATIONS == 10
    assert len(lookups) == 10
    locations = result["schema:location"]
    assert len(locations) == 15
    # The rest passes through as it came.
    assert locations[10:] == places[10:]


@pytest.mark.asyncio
async def test_places_with_coordinates_do_not_use_up_the_limit():
    service = GeocodingService()
    lookups = []

    async def geocode(address, language="de"):
        lookups.append(address)
        return None

    service.geocode_address = geocode
    known = [
        {"name": f"Ort {i}", "geo": {"latitude": 50.0 + i, "longitude": 11.0}}
        for i in range(10)
    ]
    new = ["Erfurter Str. 1, 99423 Weimar", "Unter den Linden 1, 10117 Berlin"]

    await service.enrich_metadata_with_geocoding({"schema:location": known + new})

    assert lookups == new
