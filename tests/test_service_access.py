"""
Which text extraction and which geocoder the agent calls - and with which key.

Every service exists once per environment. A deployment that uses the B-API
reaches them through the B-API's own paths (/api/v1/proxy/...): the staging
B-API leads to the staging services, the production B-API to the production
ones, and the calls carry the same key as the LLM calls. Without a B-API the
text extraction follows the repository address. METADATA_AGENT_B_API_SERVICES
overrides the choice, an explicit METADATA_AGENT_TEXT_EXTRACTION_API_URL wins
over both.
"""

import httpx
import pytest

from src.config import Settings
from src.services import geocoding_service as geo_module
from src.services import input_source_service as input_module
from src.services.geocoding_service import GeocoderRefused, GeocodingService
from src.services.input_source_service import InputSourceService, TextExtractionError
from src.metrics import upstream_service
from src.services.llm_auth import b_api_headers_for, forwarded_authorization
from src.utils.request_id import traced
from conftest import use_settings

STAGING_B_API = "https://b-api.staging.openeduhub.net"
PROD_B_API = "https://b-api.prod.openeduhub.net"


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# ------------------------------------------------------------ which address


@pytest.mark.parametrize("b_api", [STAGING_B_API, PROD_B_API])
def test_with_a_b_api_the_services_are_reached_through_it(b_api):
    settings = _settings(llm_provider="b-api-openai", b_api_base_url=b_api)

    assert settings.text_extraction_api_url == f"{b_api}/api/v1/proxy/text-extraction"
    assert settings.geocoder_api_url == f"{b_api}/api/v1/proxy/photon_geocoding/api"


@pytest.mark.parametrize(
    "repository, extractor",
    [
        (
            "https://repository.staging.openeduhub.net/edu-sharing/rest",
            "https://text-extraction.staging.openeduhub.net",
        ),
        (
            "https://redaktion.openeduhub.net/edu-sharing/rest",
            "https://text-extraction.prod.openeduhub.net",
        ),
    ],
)
def test_without_a_b_api_the_text_extraction_follows_the_repository(
    repository, extractor
):
    settings = _settings(llm_provider="openai", repository_url=repository)

    assert settings.text_extraction_api_url == extractor
    assert settings.geocoder_api_url == "https://photon.komoot.io/api"


def test_an_unknown_repository_names_no_text_extraction_and_says_so():
    settings = _settings(
        llm_provider="openai", repository_url="https://repo.example/edu-sharing/rest"
    )

    assert settings.text_extraction_api_url == ""
    assert any(
        "METADATA_AGENT_TEXT_EXTRACTION_API_URL" in warning
        for warning in settings.config_warnings()
    )


@pytest.mark.parametrize("provider", ["b-api-openai", "b-api-router", "openai"])
def test_an_explicit_text_extraction_address_always_wins(provider):
    settings = _settings(
        llm_provider=provider,
        text_extraction_api_url="http://bildungs-api-services-full-text.b-api/",
    )

    assert (
        settings.text_extraction_api_url
        == "http://bildungs-api-services-full-text.b-api"
    )


def test_the_choice_can_be_forced_either_way():
    direct = _settings(llm_provider="b-api-openai", b_api_services="direct")
    proxy = _settings(llm_provider="openai", b_api_services="proxy")

    assert (
        direct.text_extraction_api_url
        == "https://text-extraction.staging.openeduhub.net"
    )
    assert direct.geocoder_api_url == "https://photon.komoot.io/api"
    assert proxy.text_extraction_api_url == (
        f"{STAGING_B_API}/api/v1/proxy/text-extraction"
    )


def test_auto_is_the_default():
    assert _settings().b_api_services == "auto"


# ------------------------------------------------------------ which key


async def _public(url):
    return url


def _extractor(monkeypatch, settings, seen, status=200):
    def handler(request):
        seen.append(request)
        return httpx.Response(status, json={"text": "Ein Text"})

    monkeypatch.setattr(input_module, "require_public_http_url", _public)
    service = InputSourceService()
    service.settings = settings
    service.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return service


@pytest.mark.asyncio
async def test_through_the_b_api_the_callers_key_goes_along(monkeypatch):
    seen = []
    service = _extractor(monkeypatch, _settings(b_api_key="own-key"), seen)

    with forwarded_authorization("Bearer user-key"):
        await service.fetch_from_url("https://example.org/page")

    (request,) = seen
    assert str(request.url) == f"{STAGING_B_API}/api/v1/proxy/text-extraction/from-url"
    assert request.headers["authorization"] == "Bearer user-key"
    # Exactly one key header, as for the LLM calls.
    assert "x-api-key" not in request.headers


@pytest.mark.asyncio
async def test_without_a_forwarded_key_the_own_key_is_used(monkeypatch):
    seen = []
    service = _extractor(monkeypatch, _settings(b_api_key="own-key"), seen)

    await service.fetch_from_url("https://example.org/page")

    assert seen[0].headers["x-api-key"] == "own-key"
    assert "authorization" not in seen[0].headers


@pytest.mark.asyncio
async def test_a_service_outside_the_b_api_never_gets_the_key(monkeypatch):
    seen = []
    settings = _settings(
        b_api_key="own-key",
        text_extraction_api_url="http://bildungs-api-services-full-text.b-api",
    )
    service = _extractor(monkeypatch, settings, seen)

    with forwarded_authorization("Bearer user-key"):
        await service.fetch_from_url("https://example.org/page")

    assert "authorization" not in seen[0].headers
    assert "x-api-key" not in seen[0].headers


@pytest.mark.asyncio
async def test_no_key_at_all_is_named_before_any_call(monkeypatch):
    seen = []
    service = _extractor(monkeypatch, _settings(b_api_key=""), seen)

    with pytest.raises(TextExtractionError) as raised:
        await service.fetch_from_url("https://example.org/page")

    assert raised.value.code == "TEXT_EXTRACTION_AUTH_MISSING"
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_a_key_the_b_api_refuses_is_named(monkeypatch, status):
    seen = []
    service = _extractor(monkeypatch, _settings(b_api_key="own-key"), seen, status)

    with pytest.raises(TextExtractionError) as raised:
        await service.fetch_from_url("https://example.org/page")

    assert raised.value.code == "TEXT_EXTRACTION_AUTH_REFUSED"
    assert "text-extraction" in raised.value.message
    assert "own-key" not in raised.value.message


@pytest.mark.asyncio
async def test_without_any_address_the_answer_says_what_to_set(monkeypatch):
    seen = []
    settings = _settings(
        llm_provider="openai", repository_url="https://repo.example/edu-sharing/rest"
    )
    service = _extractor(monkeypatch, settings, seen)

    with pytest.raises(TextExtractionError) as raised:
        await service.fetch_from_url("https://example.org/page")

    assert raised.value.code == "TEXT_EXTRACTION_NOT_CONFIGURED"
    assert seen == []


# ------------------------------------------------------------ geocoder


class _Calls(list):
    """The requests the geocoder got; `status` is what it answers."""

    status = 200


@pytest.fixture
def geocoder(monkeypatch):
    """Route the geocoder's HTTP client to a handler that records the calls."""
    seen = _Calls()

    def handler(request):
        seen.append(request)
        feature = {
            "geometry": {"coordinates": [11.3297921, 50.9793301]},
            "properties": {"city": "Weimar"},
        }
        return httpx.Response(seen.status, json={"features": [feature]})

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(geo_module.httpx, "AsyncClient", client)
    return seen


@pytest.mark.asyncio
async def test_the_geocoder_is_called_through_the_b_api_with_the_key(
    monkeypatch, geocoder
):
    use_settings(monkeypatch, _settings())

    with forwarded_authorization("Bearer user-key"):
        result = await GeocodingService().geocode_address("Weimar")

    (request,) = geocoder
    assert result["latitude"] == 50.9793301
    assert str(request.url).startswith(
        f"{STAGING_B_API}/api/v1/proxy/photon_geocoding/api?"
    )
    assert request.headers["authorization"] == "Bearer user-key"


@pytest.mark.asyncio
async def test_photon_itself_never_gets_a_key_or_trace_headers(monkeypatch, geocoder):
    use_settings(monkeypatch, _settings(b_api_services="direct", b_api_key="own-key"))

    # Inside a request with tracing headers - only then their absence says
    # anything (audit 13.11., T-01).
    with traced({"x-client-trace-id": "trace-abc"}):
        with forwarded_authorization("Bearer user-key"):
            await GeocodingService().geocode_address("Weimar")

    (request,) = geocoder
    assert request.url.host == "photon.komoot.io"
    for header in ("authorization", "x-api-key", "x-b3-traceid", "x-request-id"):
        assert header not in request.headers


# A key the B-API does not let use the geocoder fails for every place alike.
# Audit 13.11., L-15: each place cost a second and a log line "Photon API
# error", and the answer said nothing - places simply had no coordinates.

PLACES = {"schema:location": ["Weimar", "Erfurt", "Jena"]}


def _fast(service: GeocodingService) -> GeocodingService:
    service.RATE_LIMIT_MS = 0
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429])
async def test_a_refusal_of_the_b_api_is_reported_once_and_ends_the_lookups(
    monkeypatch, geocoder, status
):
    use_settings(monkeypatch, _settings())
    geocoder.status = status
    warnings: list[str] = []

    result = await _fast(GeocodingService()).enrich_metadata_with_geocoding(
        dict(PLACES), warnings=warnings
    )

    assert len(geocoder) == 1
    assert result["schema:location"] == PLACES["schema:location"]
    (warning,) = warnings
    assert str(status) in warning and "photon_geocoding" in warning


@pytest.mark.asyncio
async def test_without_any_key_no_call_is_made_and_no_slot_is_taken(
    monkeypatch, geocoder
):
    use_settings(monkeypatch, _settings(b_api_key=""))
    service = GeocodingService()
    warnings: list[str] = []

    await service.enrich_metadata_with_geocoding(dict(PLACES), warnings=warnings)

    assert geocoder == []
    assert service._next_slot == 0.0
    assert len(warnings) == 1 and "Schlüssel" in warnings[0]


@pytest.mark.asyncio
async def test_a_refusal_is_an_error_of_its_own_for_a_single_lookup(
    monkeypatch, geocoder
):
    use_settings(monkeypatch, _settings())
    geocoder.status = 403

    with pytest.raises(GeocoderRefused):
        await _fast(GeocodingService()).geocode_address("Weimar")


@pytest.mark.asyncio
async def test_photon_itself_answering_403_is_no_refused_key(monkeypatch, geocoder):
    use_settings(monkeypatch, _settings(b_api_services="direct"))
    geocoder.status = 403
    warnings: list[str] = []

    await _fast(GeocodingService()).enrich_metadata_with_geocoding(
        dict(PLACES), warnings=warnings
    )

    # No key was sent, so none was refused: every place is tried, as before.
    assert len(geocoder) == 3
    assert warnings == []


def test_a_foreign_host_named_staging_gets_no_openeduhub_service():
    """Audit 13.11., L-23: "staging" in any host name counted as OpenEduHub's."""
    settings = _settings(
        llm_provider="openai",
        repository_url="https://repository.staging.example.org/edu-sharing/rest",
    )

    assert settings.text_extraction_api_url == ""


# ------------------------------------------------------------ one B-API only
#
# Audit 13.11., S-24: text extraction and geocoder followed b_api_base_url,
# the LLM calls their own base address. With only the LLM base changed, the
# key of a production deployment went to the default B-API (staging) as well.

CLUSTER_LLM = "http://bildungs-api.b-api/api/v1/llm/openai"


def test_auto_does_not_send_the_key_to_another_b_api_than_the_llm_calls():
    settings = _settings(llm_provider="b-api-openai", b_api_openai_base=CLUSTER_LLM)

    assert settings.services_via_b_api is False
    assert settings.geocoder_api_url == "https://photon.komoot.io/api"
    assert not settings.text_extraction_api_url.startswith(STAGING_B_API)
    assert any(
        "METADATA_AGENT_B_API_BASE_URL" in warning and "direkt" in warning
        for warning in settings.config_warnings()
    )


def test_the_router_base_counts_for_the_router():
    settings = _settings(
        llm_provider="b-api-router",
        b_api_router_base="http://bildungs-api.b-api/api/v1/llm/router",
    )

    assert settings.services_via_b_api is False


def test_an_llm_base_below_the_b_api_address_changes_nothing():
    settings = _settings(
        llm_provider="b-api-openai",
        b_api_base_url=PROD_B_API,
        b_api_openai_base=f"{PROD_B_API}/api/v1/llm/openai/",
    )

    assert settings.services_via_b_api is True
    assert not any("direkt" in w for w in settings.config_warnings())


def test_forcing_the_proxy_towards_another_b_api_is_warned_about():
    settings = _settings(
        llm_provider="b-api-openai",
        b_api_openai_base=CLUSTER_LLM,
        b_api_services="proxy",
    )

    assert settings.services_via_b_api is True
    assert any(
        "Schlüssel" in warning and STAGING_B_API in warning
        for warning in settings.config_warnings()
    )


# ------------------------------------------------------------ edges (T-01)


@pytest.mark.asyncio
async def test_through_the_b_api_the_geocoder_call_carries_the_trace(
    monkeypatch, geocoder
):
    use_settings(monkeypatch, _settings())

    with traced({"x-client-trace-id": "trace-abc"}):
        await _fast(GeocodingService()).geocode_address("Weimar")

    (request,) = geocoder
    assert request.headers["x-client-trace-id"] == "trace-abc"
    assert "x-b3-traceid" in request.headers


@pytest.mark.parametrize(
    "url",
    [
        "https://b-api.staging.openeduhub.net.evil.example/api/v1/proxy/x",
        "https://b-api.staging.openeduhub.net:8443/api/v1/proxy/x",
        "https://b-api.staging.openeduhub.netx/api",
        "http://b-api.staging.openeduhub.net/api/v1/proxy/x",
        "https://B-API.staging.openeduhub.net/api/v1/proxy/x",
        "https://other.example/?next=https://b-api.staging.openeduhub.net/",
    ],
)
def test_the_key_only_goes_to_the_b_api_address_itself(url):
    """Anything that merely looks like it gets no key - and so answers 401."""
    assert b_api_headers_for(url, _settings(b_api_key="own-key")) == {}


@pytest.mark.parametrize(
    "url",
    [
        "https://b-api.staging.openeduhub.net",
        "https://b-api.staging.openeduhub.net/api/v1/proxy/text-extraction/from-url",
    ],
)
def test_the_b_api_address_and_everything_below_it_get_the_key(url):
    assert b_api_headers_for(url, _settings(b_api_key="own-key")) == {
        "X-API-KEY": "own-key"
    }


@pytest.mark.parametrize(
    "path, service",
    [
        ("/api/v1/proxy/text-extraction/from-url", "text-extraction"),
        ("/api/v1/proxy/photon_geocoding/api?q=Weimar", "geocoder"),
        ("/api/v1/llm/openai/chat/completions", "llm"),
    ],
)
def test_calls_through_the_b_api_are_counted_under_their_own_service(path, service):
    """The alert rules tell the services apart by this label."""
    assert upstream_service(f"{STAGING_B_API}{path}", _settings()) == service
