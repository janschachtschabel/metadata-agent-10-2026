"""
Geocoding copes with any shape of a location and opens one client per request (audit 14.11.2026).

- L-40: a location whose `address` or `geo` is text raised an AttributeError
  that ended the geocoding of the whole request ("Geocoding failed: 'str'
  object has no attribute 'get'"). The text of an address is the address to
  look up; a `geo` that is no object holds no coordinates; whatever else goes
  wrong costs that one location, with a warning. Latitude 0 is a coordinate.
- L-41 (P-13): every lookup built its own HTTP client - 420 ms of TLS setup
  each, synchronously, up to ten per request. One client serves the lookups
  of a request, and every client shares one TLS context.
- L-N: a free-form `language` went to Photon as it came; Photon answers 400
  for a language it does not know, and the place stayed without coordinates.
"""

import httpx
import pytest

from src.services import geocoding_service as geo_module
from src.services import llm_service as llm_module
from src.services.geocoding_service import GeocodingService
from src.services.llm_service import LLMService
from src.config import Settings
from src.utils.tls_context import shared_tls_context
from conftest import use_settings

WEIMAR = {"latitude": 50.9793301, "longitude": 11.3297921}
ANSWER = {
    "features": [
        {
            "geometry": {"coordinates": [11.3297921, 50.9793301]},
            "properties": {"city": "Weimar", "countrycode": "de"},
        }
    ]
}


@pytest.fixture
def photon(monkeypatch):
    """A stand-in Photon; records the requests and the clients that were built."""
    state = {"requests": [], "clients": [], "fail_for": None}

    def handler(request):
        state["requests"].append(request)
        if state["fail_for"] and state["fail_for"] in str(request.url):
            raise RuntimeError("kaputt")
        return httpx.Response(200, json=ANSWER)

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        state["clients"].append(kwargs)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(geo_module.httpx, "AsyncClient", client)
    use_settings(monkeypatch, Settings(_env_file=None, b_api_services="direct"))
    return state


def _service():
    service = GeocodingService()
    service.RATE_LIMIT_MS = 0
    return service


def _queries(photon):
    return [request.url.params["q"] for request in photon["requests"]]


# ------------------------------------------------------------ L-40 shapes


async def test_an_address_given_as_text_is_looked_up(photon):
    warnings: list[str] = []
    metadata = {
        "schema:location": [
            {"name": "Rathaus", "address": "Markt 1, 99423 Weimar"},
            {"name": "Stadthalle"},
        ]
    }

    result = await _service().enrich_metadata_with_geocoding(
        metadata, "de", warnings=warnings
    )

    first, second = result["schema:location"]
    assert _queries(photon) == ["Markt 1, 99423 Weimar", "Stadthalle"]
    assert first["geo"] == WEIMAR
    # What the caller wrote stays; only the coordinates are added.
    assert first["address"] == "Markt 1, 99423 Weimar"
    assert second["geo"] == WEIMAR
    assert warnings == []


@pytest.mark.parametrize("geo", ["50.9,11.3", 7, ["50.9", "11.3"], None, {}])
async def test_a_geo_that_is_no_object_holds_no_coordinates(photon, geo):
    metadata = {"schema:location": [{"name": "Weimar", "geo": geo}]}

    result = await _service().enrich_metadata_with_geocoding(metadata, "de")

    assert _queries(photon) == ["Weimar"]
    assert result["schema:location"][0]["geo"] == WEIMAR


@pytest.mark.parametrize("latitude", [0, 0.0, -33.9, 50.97])
async def test_a_location_with_a_latitude_is_not_looked_up(photon, latitude):
    location = {"name": "Äquator", "geo": {"latitude": latitude, "longitude": 9.5}}

    result = await _service().enrich_metadata_with_geocoding(
        {"schema:location": [dict(location)]}, "de"
    )

    assert photon["requests"] == []
    assert result["schema:location"] == [location]


@pytest.mark.parametrize("latitude", [None, ""])
async def test_an_empty_latitude_is_no_coordinate(photon, latitude):
    metadata = {"schema:location": [{"name": "Weimar", "geo": {"latitude": latitude}}]}

    result = await _service().enrich_metadata_with_geocoding(metadata, "de")

    assert result["schema:location"][0]["geo"] == WEIMAR


async def test_a_location_that_cannot_be_processed_costs_only_itself(
    photon, monkeypatch
):
    service = _service()
    original = service._enrich_single_location

    async def failing(location, language):
        if location == "Kaputt":
            raise ValueError("unerwartete Form")
        return await original(location, language)

    monkeypatch.setattr(service, "_enrich_single_location", failing)
    warnings: list[str] = []
    metadata = {"schema:location": ["Weimar", "Kaputt", "Erfurt"]}

    result = await service.enrich_metadata_with_geocoding(
        metadata, "de", warnings=warnings
    )

    first, second, third = result["schema:location"]
    assert first["geo"] == WEIMAR and third["geo"] == WEIMAR
    assert second == "Kaputt"
    assert warnings == [
        "Geokodierung: 1 Ort(e) konnten nicht verarbeitet werden und bleiben "
        "unverändert (ValueError)."
    ]


@pytest.mark.parametrize("address", [7, ["Markt 1"], True])
async def test_an_address_of_another_type_is_ignored(photon, address):
    metadata = {"schema:location": [{"name": "Weimar", "address": address}]}

    result = await _service().enrich_metadata_with_geocoding(metadata, "de")

    assert _queries(photon) == ["Weimar"]
    assert result["schema:location"][0]["geo"] == WEIMAR


# ------------------------------------------------------------ L-41 one client


async def test_the_lookups_of_a_request_share_one_client(photon):
    metadata = {"schema:location": [f"Ort {i}" for i in range(6)]}

    await _service().enrich_metadata_with_geocoding(metadata, "de")

    assert len(photon["requests"]) == 6
    assert len(photon["clients"]) == 1


async def test_a_request_without_a_lookup_builds_no_client(photon):
    metadata = {"schema:location": [{"name": "Ort", "geo": dict(WEIMAR)}]}

    await _service().enrich_metadata_with_geocoding(metadata, "de")

    assert photon["clients"] == []


async def test_a_single_lookup_builds_and_closes_its_own_client(photon):
    service = _service()

    assert (await service.geocode_address("Weimar"))["latitude"] == WEIMAR["latitude"]
    assert (await service.geocode_address("Erfurt"))["latitude"] == WEIMAR["latitude"]

    assert len(photon["clients"]) == 2


async def test_every_geocoder_client_shares_the_tls_context(photon):
    service = _service()

    await service.geocode_address("Weimar")
    await service.enrich_metadata_with_geocoding({"schema:location": ["Ort"]}, "de")

    assert [kwargs["verify"] for kwargs in photon["clients"]] == [
        shared_tls_context(),
        shared_tls_context(),
    ]


def test_llm_clients_of_overrides_share_the_tls_context(monkeypatch):
    cfg = Settings(_env_file=None, b_api_key="own-key")
    use_settings(monkeypatch, cfg)
    built = []
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        built.append(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(llm_module.httpx, "AsyncClient", client)

    LLMService(llm_provider="b-api-openai", llm_model="gpt-6-luna")
    LLMService(llm_provider="b-api-openai", llm_model="gpt-6-luna-mini")

    assert [kwargs["verify"] for kwargs in built] == [shared_tls_context()] * 2


def test_the_tls_context_is_built_once_and_verifies_certificates():
    import ssl

    context = shared_tls_context()

    assert context is shared_tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


# ------------------------------------------------------------ language


@pytest.mark.parametrize(
    "language, sent",
    [("de", "de"), ("en", "en"), ("fr", "fr"), ("DE", "de"), ("en-GB", "en")],
)
async def test_a_language_photon_knows_is_sent(photon, language, sent):
    await _service().geocode_address("Weimar", language)

    assert photon["requests"][0].url.params["lang"] == sent


@pytest.mark.parametrize("language", ["es", "xx", "", "deutsch", "d" * 500, "de&q=x"])
async def test_any_other_language_is_left_out(photon, language):
    result = await _service().geocode_address("Weimar", language)

    assert "lang" not in photon["requests"][0].url.params
    assert result["latitude"] == WEIMAR["latitude"]
