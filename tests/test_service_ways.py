"""
The ways to text extraction and geocoder, and how the agent goes around a
refusal (src/services/service_ways.py).

Staging 16.11.: the B-API forwards a delegation token (forwardDelegation), not
the caller's key. The token reached the LLM endpoints and got 401 on
photon_geocoding, while the agent's own key and Photon itself answered. With
METADATA_AGENT_B_API_SERVICES=auto a call now tries: the forwarded credential,
the agent's own key, the service directly. A refusal is remembered for a while,
so the following requests start with the way that works.
"""

import httpx
import pytest

from src.config import Settings
from src.services import geocoding_service as geo_module
from src.services import input_source_service as input_module
from src.services import service_ways
from src.services.geocoding_service import GeocodingService
from src.services.input_source_service import InputSourceService, TextExtractionError
from src.services.llm_auth import forwarded_authorization
from src.services.service_ways import (
    REFUSALS,
    geocoder_ways,
    text_extraction_ways,
)
from conftest import use_settings

B_API = "b-api.staging.openeduhub.net"
PHOTON = "photon.komoot.io"
DIRECT_EXTRACTION = "text-extraction.staging.openeduhub.net"
FORWARDED = "Bearer delegation-token"


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{"b_api_key": "own-key", **overrides})


def _way_names(ways) -> list[str]:
    return [w.name for w in ways]


def _credential(request) -> str:
    """Which way a request took: forwarded, own or direct."""
    if "authorization" in request.headers:
        return "forwarded"
    if "x-api-key" in request.headers:
        return "own"
    return "direct"


# ------------------------------------------------------------ which ways


def test_auto_tries_forwarded_own_and_direct_in_this_order():
    with forwarded_authorization(FORWARDED):
        ways = geocoder_ways(_settings())

    assert _way_names(ways) == ["forwarded", "own", "direct"]
    assert ways[0].headers == {"Authorization": FORWARDED}
    assert ways[1].headers == {"X-API-KEY": "own-key"}
    assert ways[2].url == f"https://{PHOTON}/api" and ways[2].headers == {}


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"b_api_auth_mode": "forward"}, ["forwarded", "direct"]),
        ({"b_api_auth_mode": "static"}, ["own", "direct"]),
        ({"b_api_services": "proxy"}, ["forwarded", "own"]),
        ({"b_api_services": "direct"}, ["direct"]),
        ({"b_api_key": ""}, ["forwarded", "direct"]),
    ],
)
def test_the_settings_decide_which_ways_there_are(overrides, expected):
    with forwarded_authorization(FORWARDED):
        assert _way_names(geocoder_ways(_settings(**overrides))) == expected


def test_the_direct_text_extraction_follows_the_repository():
    staging = text_extraction_ways(_settings())
    prod = text_extraction_ways(
        _settings(repository_url="https://redaktion.openeduhub.net/edu-sharing/rest")
    )
    foreign = text_extraction_ways(
        _settings(repository_url="https://repo.example/edu-sharing/rest")
    )

    assert staging[-1].url == f"https://{DIRECT_EXTRACTION}"
    assert prod[-1].url == "https://text-extraction.prod.openeduhub.net"
    assert _way_names(foreign) == ["own"]


def test_an_address_of_its_own_is_the_only_way_and_gets_no_key():
    settings = _settings(text_extraction_api_url="http://full-text.b-api")

    with forwarded_authorization(FORWARDED):
        (way,) = text_extraction_ways(settings)

    assert way.name == "direct" and way.headers == {}


def test_a_refused_way_is_skipped_for_a_while_then_tried_again(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(service_ways.time, "monotonic", lambda: now[0])
    REFUSALS.refused("geocoder", "forwarded", "HTTP 401", credential=FORWARDED)

    with forwarded_authorization(FORWARDED):
        assert _way_names(geocoder_ways(_settings())) == ["own", "direct"]
        now[0] += service_ways.REFUSED_FOR_SECONDS
        assert _way_names(geocoder_ways(_settings())) == ["forwarded", "own", "direct"]


def test_a_refusal_counts_for_its_service_only():
    REFUSALS.refused("geocoder", "forwarded", "HTTP 401", credential=FORWARDED)

    with forwarded_authorization(FORWARDED):
        assert _way_names(text_extraction_ways(_settings()))[0] == "forwarded"


def test_when_every_way_is_remembered_as_refused_all_are_tried_again():
    REFUSALS.refused("geocoder", "forwarded", "HTTP 401", credential=FORWARDED)
    REFUSALS.refused("geocoder", "own", "HTTP 401")

    with forwarded_authorization(FORWARDED):
        ways = geocoder_ways(_settings(b_api_services="proxy"))

    assert _way_names(ways) == ["forwarded", "own"]


# ------------------------------------------------------------ geocoder


@pytest.fixture
def upstream(monkeypatch):
    """
    Answers by way: `answers[way]` is a status or an exception class; a way
    without an entry answers 200 with a place.
    """
    state = {"answers": {}, "calls": []}

    def handler(request):
        way = _credential(request)
        state["calls"].append((request.url.host, way))
        answer = state["answers"].get(way, 200)
        if isinstance(answer, type):
            raise answer("no way", request=request)
        if answer != 200:
            return httpx.Response(answer, json={"error": "x"})
        if request.url.host in (PHOTON, B_API) and request.method == "GET":
            feature = {"geometry": {"coordinates": [11.33, 50.98]}, "properties": {}}
            return httpx.Response(200, json={"features": [feature]})
        return httpx.Response(200, json={"text": state.get("text", f"Text über {way}")})

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(geo_module.httpx, "AsyncClient", client)
    monkeypatch.setattr(input_module.httpx, "AsyncClient", client)
    return state


PLACES = {"schema:location": ["Weimar", "Erfurt"]}


async def _geocode(warnings=None):
    service = GeocodingService()
    service.RATE_LIMIT_MS = 0
    with forwarded_authorization(FORWARDED):
        return await service.enrich_metadata_with_geocoding(
            {"schema:location": list(PLACES["schema:location"])}, warnings=warnings
        )


def _has_coordinates(result) -> bool:
    return all(p["geo"]["latitude"] == 50.98 for p in result["schema:location"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_geocoder_a_refused_token_falls_back_to_the_own_key(
    monkeypatch, upstream, status
):
    use_settings(monkeypatch, _settings())
    upstream["answers"] = {"forwarded": status}

    result = await _geocode()

    assert _has_coordinates(result)
    assert upstream["calls"] == [(B_API, "forwarded"), (B_API, "own"), (B_API, "own")]


@pytest.mark.asyncio
async def test_geocoder_the_next_request_starts_with_the_way_that_works(
    monkeypatch, upstream
):
    use_settings(monkeypatch, _settings())
    upstream["answers"] = {"forwarded": 401}
    await _geocode()
    upstream["calls"].clear()

    await _geocode()

    assert upstream["calls"] == [(B_API, "own"), (B_API, "own")]


@pytest.mark.asyncio
async def test_geocoder_both_keys_refused_goes_to_photon_and_remembers_it(
    monkeypatch, upstream
):
    use_settings(monkeypatch, _settings())
    upstream["answers"] = {"forwarded": 401, "own": 403}
    warnings: list[str] = []

    result = await _geocode(warnings)

    assert _has_coordinates(result) and warnings == []
    assert upstream["calls"] == [
        (B_API, "forwarded"),
        (B_API, "own"),
        (PHOTON, "direct"),
        (PHOTON, "direct"),
    ]
    upstream["calls"].clear()
    await _geocode()
    assert upstream["calls"] == [(PHOTON, "direct"), (PHOTON, "direct")]


@pytest.mark.asyncio
async def test_geocoder_proxy_mode_never_goes_to_photon(monkeypatch, upstream):
    use_settings(monkeypatch, _settings(b_api_services="proxy"))
    upstream["answers"] = {"forwarded": 401, "own": 401}
    warnings: list[str] = []

    result = await _geocode(warnings)

    assert result["schema:location"] == PLACES["schema:location"]
    assert all(host == B_API for host, _ in upstream["calls"])
    (warning,) = warnings
    assert "401" in warning and "photon_geocoding" in warning


@pytest.mark.asyncio
async def test_geocoder_a_broken_b_api_is_not_tried_with_another_key(
    monkeypatch, upstream
):
    """A 502 is no question of the key: straight to Photon, and not remembered."""
    use_settings(monkeypatch, _settings())
    upstream["answers"] = {"forwarded": 502}

    result = await _geocode()

    assert _has_coordinates(result)
    assert upstream["calls"] == [
        (B_API, "forwarded"),
        (PHOTON, "direct"),
        (PHOTON, "direct"),
    ]
    assert REFUSALS.snapshot() == {}


# ------------------------------------------------------------ text extraction


async def _public(url):
    return url


async def _extract(monkeypatch, settings, forwarded=FORWARDED):
    use_settings(monkeypatch, settings)
    monkeypatch.setattr(input_module, "require_public_http_url", _public)
    service = InputSourceService()
    try:
        with forwarded_authorization(forwarded):
            return await service.fetch_from_url("https://example.org/page")
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_extraction_a_refused_token_falls_back_to_the_own_key(
    monkeypatch, upstream, status
):
    upstream["answers"] = {"forwarded": status}

    text = await _extract(monkeypatch, _settings())

    assert text == "Text über own"
    assert upstream["calls"] == [(B_API, "forwarded"), (B_API, "own")]


@pytest.mark.asyncio
async def test_extraction_both_keys_refused_goes_to_the_service_itself(
    monkeypatch, upstream
):
    upstream["answers"] = {"forwarded": 401, "own": 401}

    text = await _extract(monkeypatch, _settings())

    assert text == "Text über direct"
    assert upstream["calls"][-1] == (DIRECT_EXTRACTION, "direct")


@pytest.mark.asyncio
async def test_extraction_when_every_way_fails_the_first_reason_is_named(
    monkeypatch, upstream
):
    upstream["answers"] = {"forwarded": 401, "own": 401, "direct": httpx.ConnectError}

    with pytest.raises(TextExtractionError) as raised:
        await _extract(monkeypatch, _settings())

    assert raised.value.code == "TEXT_EXTRACTION_AUTH_REFUSED"
    assert len(upstream["calls"]) == 3


@pytest.mark.asyncio
async def test_extraction_an_unreachable_b_api_goes_straight_to_the_service(
    monkeypatch, upstream
):
    upstream["answers"] = {"forwarded": httpx.ConnectError}

    text = await _extract(monkeypatch, _settings())

    assert text == "Text über direct"
    assert upstream["calls"] == [(B_API, "forwarded"), (DIRECT_EXTRACTION, "direct")]


@pytest.mark.asyncio
async def test_extraction_a_slow_page_is_not_fetched_twice(monkeypatch, upstream):
    """A read timeout is the page's, not the way's: another way waits as long."""
    upstream["answers"] = {"forwarded": httpx.ReadTimeout}

    with pytest.raises(TextExtractionError) as raised:
        await _extract(monkeypatch, _settings())

    assert raised.value.code == "TEXT_EXTRACTION_TIMEOUT"
    assert len(upstream["calls"]) == 1


@pytest.mark.asyncio
async def test_extraction_proxy_mode_names_the_refusal(monkeypatch, upstream):
    upstream["answers"] = {"forwarded": 403, "own": 403}

    with pytest.raises(TextExtractionError) as raised:
        await _extract(monkeypatch, _settings(b_api_services="proxy"))

    assert raised.value.code == "TEXT_EXTRACTION_AUTH_REFUSED"
    assert all(host == B_API for host, _ in upstream["calls"])


@pytest.mark.asyncio
async def test_extraction_without_any_key_auto_asks_the_service_itself(
    monkeypatch, upstream
):
    text = await _extract(monkeypatch, _settings(b_api_key=""), forwarded=None)

    assert text == "Text über direct"
    assert upstream["calls"] == [(DIRECT_EXTRACTION, "direct")]


@pytest.mark.asyncio
async def test_extraction_without_any_key_proxy_mode_calls_nothing(
    monkeypatch, upstream
):
    with pytest.raises(TextExtractionError) as raised:
        await _extract(
            monkeypatch, _settings(b_api_key="", b_api_services="proxy"), forwarded=None
        )

    assert raised.value.code == "TEXT_EXTRACTION_AUTH_MISSING"
    assert upstream["calls"] == []


@pytest.mark.asyncio
async def test_extraction_a_page_without_text_is_not_fetched_twice(
    monkeypatch, upstream
):
    upstream["text"] = ""

    with pytest.raises(TextExtractionError) as raised:
        await _extract(monkeypatch, _settings())

    assert raised.value.code == "TEXT_EXTRACTION_EMPTY"
    assert len(upstream["calls"]) == 1


@pytest.mark.asyncio
async def test_the_diagnostics_name_the_refused_ways():
    from src.services.diagnostics import check_dependencies

    REFUSALS.refused("geocoder", "forwarded", "HTTP 401")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    )

    report = await check_dependencies(_settings(), client)
    await client.aclose()

    assert list(report["refused_ways"]) == ["geocoder:forwarded"]
    assert 0 < report["refused_ways"]["geocoder:forwarded"] <= 600


# ------------------------------------------------------------ review 16.11.


def test_a_refused_forwarded_credential_does_not_switch_other_callers():
    """Caller A's bad token must not make caller B's good one skipped."""
    REFUSALS.refused("geocoder", "forwarded", "HTTP 401", credential="Bearer bad")

    with forwarded_authorization("Bearer bad"):
        assert _way_names(geocoder_ways(_settings())) == ["own", "direct"]
    with forwarded_authorization(FORWARDED):
        assert _way_names(geocoder_ways(_settings())) == ["forwarded", "own", "direct"]


def test_a_refused_own_key_is_remembered_for_every_caller():
    REFUSALS.refused("geocoder", "own", "HTTP 401")

    with forwarded_authorization(FORWARDED):
        assert _way_names(geocoder_ways(_settings())) == ["forwarded", "direct"]
    with forwarded_authorization("Bearer other"):
        assert _way_names(geocoder_ways(_settings())) == ["forwarded", "direct"]


def test_a_way_does_not_show_its_credential():
    with forwarded_authorization(FORWARDED):
        ways = geocoder_ways(_settings())

    assert "delegation-token" not in repr(ways) and "own-key" not in repr(ways)


@pytest.mark.asyncio
async def test_geocoder_an_exhausted_quota_of_the_caller_is_not_paid_by_the_agent(
    monkeypatch, upstream
):
    use_settings(monkeypatch, _settings())
    upstream["answers"] = {"forwarded": 429}

    result = await _geocode()

    assert _has_coordinates(result)
    assert "own" not in [way for _, way in upstream["calls"]]


@pytest.mark.asyncio
async def test_extraction_an_exhausted_quota_of_the_caller_is_not_paid_by_the_agent(
    monkeypatch, upstream
):
    upstream["answers"] = {"forwarded": 429}

    text = await _extract(monkeypatch, _settings())

    assert text == "Text über direct"
    assert upstream["calls"] == [(B_API, "forwarded"), (DIRECT_EXTRACTION, "direct")]


@pytest.mark.asyncio
async def test_extraction_proxy_mode_with_an_exhausted_quota_says_so(
    monkeypatch, upstream
):
    upstream["answers"] = {"forwarded": 429}

    with pytest.raises(TextExtractionError) as raised:
        await _extract(monkeypatch, _settings(b_api_services="proxy"))

    assert raised.value.code == "TEXT_EXTRACTION_RATE_LIMITED"
    assert upstream["calls"] == [(B_API, "forwarded")]


@pytest.mark.asyncio
async def test_extraction_a_gateway_timeout_of_the_b_api_is_a_slow_page(
    monkeypatch, upstream
):
    upstream["answers"] = {"forwarded": 504}

    with pytest.raises(TextExtractionError) as raised:
        await _extract(monkeypatch, _settings())

    assert raised.value.code == "TEXT_EXTRACTION_TIMEOUT"
    assert len(upstream["calls"]) == 1


@pytest.mark.asyncio
async def test_extraction_the_same_situation_gives_the_same_code_twice(
    monkeypatch, upstream
):
    """Both keys refused, the service itself broken: refused - also when the
    second request only remembers the refusals (review 16.11.)."""
    upstream["answers"] = {"forwarded": 401, "own": 401, "direct": 500}

    codes = []
    for _ in range(2):
        with pytest.raises(TextExtractionError) as raised:
            await _extract(monkeypatch, _settings())
        codes.append(raised.value.code)

    assert codes == ["TEXT_EXTRACTION_AUTH_REFUSED"] * 2
    assert upstream["calls"][-1] == (DIRECT_EXTRACTION, "direct")


@pytest.mark.asyncio
async def test_geocoder_the_same_situation_gives_the_same_warning_twice(
    monkeypatch, upstream
):
    use_settings(monkeypatch, _settings())
    upstream["answers"] = {"forwarded": 401, "own": 401, "direct": 500}

    first, second = [], []
    await _geocode(first)
    await _geocode(second)

    assert len(first) == len(second) == 1
    assert "401" in second[0] and "500" in second[0]


@pytest.mark.asyncio
async def test_extraction_remembers_refusals_across_requests(monkeypatch, upstream):
    upstream["answers"] = {"forwarded": 403}
    await _extract(monkeypatch, _settings())
    upstream["calls"].clear()

    await _extract(monkeypatch, _settings())

    assert upstream["calls"] == [(B_API, "own")]


@pytest.mark.asyncio
async def test_extraction_static_mode_ignores_a_forwarded_header(monkeypatch, upstream):
    text = await _extract(monkeypatch, _settings(b_api_auth_mode="static"))

    assert text == "Text über own"
    assert upstream["calls"] == [(B_API, "own")]
