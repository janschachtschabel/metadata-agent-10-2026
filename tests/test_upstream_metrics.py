"""
Calls to the services the agent depends on, and the tokens it spends.

An outage of the text extraction or the repository used to show only
indirectly, as failed generations. Now every outgoing HTTP call is counted
when it is sent and again when an answer arrives, by service - the
difference is the calls that got no answer at all (timeout, refused
connection). Token usage is read from each LLM answer, including the cache
primer, which /metrics did not see before.
"""

import httpx
import pytest
from prometheus_client import REGISTRY

from src.config import Settings
from src.metrics import (
    OWN_SERVICE_HOOKS,
    THIRD_PARTY_HOOKS,
    count_upstream_request,
    upstream_service,
)
from src.services import geocoding_service as geo_module
from src.services.input_source_service import InputSourceService
from src.services.llm_service import LLMService, reset_provider_gates
from src.utils.request_id import traced
from conftest import use_settings


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        **{
            "b_api_key": "own-key",
            "b_api_base_url": "https://b-api.example",
            "repository_url": "https://repo.example/edu-sharing/rest",
            "text_extraction_api_url": "http://text-extraction.svc",
            **overrides,
        },
    )


def _sample(name, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ------------------------------------------------------------ classification


@pytest.mark.parametrize(
    "url, service",
    [
        ("https://b-api.example/api/v1/llm/openai/chat/completions", "llm"),
        ("https://b-api.example/api/v1/llm/router/chat/completions", "llm"),
        ("https://api.openai.com/v1/chat/completions", "llm"),
        ("http://text-extraction.svc/from-url", "text-extraction"),
        ("https://repo.example/edu-sharing/rest/node/v1/nodes/-home-/x", "repository"),
        ("https://photon.komoot.io/api?q=Weimar", "geocoder"),
        ("https://somewhere.example/page", "other"),
        # A prefix only counts up to a path boundary.
        ("https://repo.example/edu-sharing/rest-other/x", "other"),
    ],
)
def test_calls_are_named_by_the_service_they_go_to(url, service):
    assert upstream_service(url, _settings()) == service


# Audit 14.11., O-36: httpx sends the host in lower case and without the
# scheme's default port. A setting written otherwise matched no call: all of
# them were counted as "other", and the upstream alerts never fired.


def _written(url: str, how: str) -> str:
    scheme, _, rest = url.partition("://")
    host, slash, path = rest.partition("/")
    if how == "upper-case host":
        host = host.upper()
    elif how == "default port":
        host += ":443" if scheme == "https" else ":80"
    elif how == "upper-case scheme":
        scheme = scheme.upper()
    elif how == "blanks and a slash":
        return f"  {url}/ "
    return f"{scheme}://{host}{slash}{path}"


@pytest.mark.parametrize(
    "how",
    ["upper-case host", "default port", "upper-case scheme", "blanks and a slash"],
)
@pytest.mark.parametrize(
    "setting, url, path, service",
    [
        ("b_api_base_url", "https://b-api.example", "/api/v1/llm/openai/x", "llm"),
        (
            "b_api_base_url",
            "https://b-api.example",
            "/api/v1/proxy/text-extraction/from-url",
            "text-extraction",
        ),
        (
            "b_api_base_url",
            "https://b-api.example",
            "/api/v1/proxy/photon_geocoding/api?q=Weimar",
            "geocoder",
        ),
        ("b_api_router_base", "https://router.example/v1", "/chat/completions", "llm"),
        ("openai_api_base", "https://llm.example/v1", "/chat/completions", "llm"),
        (
            "text_extraction_api_url",
            "http://text-extraction.svc",
            "/from-url",
            "text-extraction",
        ),
        (
            "repository_url",
            "https://repo.example/edu-sharing/rest",
            "/node/v1/nodes/-home-/x",
            "repository",
        ),
    ],
)
def test_a_url_setting_is_recognised_however_it_is_written(
    setting, url, path, service, how
):
    # Derived from the B-API address only while no text extraction is named.
    derived = {"text_extraction_api_url": ""} if setting == "b_api_base_url" else {}
    settings = _settings(**{**derived, setting: _written(url, how)})
    sent = str(httpx.Request("GET", url + path).url)

    assert upstream_service(sent, settings) == service


@pytest.mark.parametrize(
    "written, kept",
    [
        # Another port, an IPv6 host, user info and the path's case stay.
        ("http://[FD00::10]:8080/", "http://[fd00::10]:8080"),
        (
            "https://user:Secret@Host.example:443/Path/",
            "https://user:Secret@host.example/Path",
        ),
        ("http://text-extraction.svc:8080", "http://text-extraction.svc:8080"),
        ("no url", "no url"),
    ],
)
def test_normalising_a_url_setting_keeps_everything_else(written, kept):
    assert _settings(repository_url=written).repository_url == kept


def test_the_label_never_carries_a_host_or_path():
    """Anything unknown shares one label, so /metrics cannot grow with traffic."""
    assert upstream_service("https://a.example/b?c=d", _settings()) == "other"


# ------------------------------------------------------------ hooks


def _client(handler, hooks) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), event_hooks=hooks)


@pytest.fixture
def settings(monkeypatch):
    cfg = _settings()
    use_settings(monkeypatch, cfg)
    return cfg


@pytest.mark.asyncio
async def test_a_call_is_counted_when_sent_and_when_answered(settings):
    sent = _sample("metadata_agent_upstream_requests_total", service="repository")
    answered = _sample(
        "metadata_agent_upstream_responses_total", service="repository", status="503"
    )

    async with _client(lambda r: httpx.Response(503), OWN_SERVICE_HOOKS) as client:
        await client.get("https://repo.example/edu-sharing/rest/node/v1/x")

    assert (
        _sample("metadata_agent_upstream_requests_total", service="repository")
        == sent + 1
    )
    assert (
        _sample(
            "metadata_agent_upstream_responses_total",
            service="repository",
            status="503",
        )
        == answered + 1
    )


@pytest.mark.asyncio
async def test_a_call_without_answer_is_sent_but_never_answered(settings):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    sent = _sample("metadata_agent_upstream_requests_total", service="text-extraction")
    durations = _sample(
        "metadata_agent_upstream_response_duration_seconds_count",
        service="text-extraction",
    )

    async with _client(refuse, OWN_SERVICE_HOOKS) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post("http://text-extraction.svc/from-url")

    assert (
        _sample("metadata_agent_upstream_requests_total", service="text-extraction")
        == sent + 1
    )
    assert (
        _sample(
            "metadata_agent_upstream_response_duration_seconds_count",
            service="text-extraction",
        )
        == durations
    )


@pytest.mark.asyncio
async def test_own_services_get_the_trace_headers_third_parties_do_not(settings):
    seen = {}

    def record(request):
        seen[str(request.url.host)] = request.headers.get("x-client-trace-id")
        return httpx.Response(200, json={})

    with traced({"x-client-trace-id": "trace-1"}):
        async with _client(record, OWN_SERVICE_HOOKS) as client:
            await client.get("https://repo.example/edu-sharing/rest/x")
        async with _client(record, THIRD_PARTY_HOOKS) as client:
            await client.get("https://photon.komoot.io/api")

    assert seen == {"repo.example": "trace-1", "photon.komoot.io": None}


# ------------------------------------------------------------ wiring


def test_the_llm_and_input_clients_count_their_calls(monkeypatch):
    cfg = _settings()
    use_settings(monkeypatch, cfg)

    for client in (
        LLMService(llm_provider="b-api-openai").http_client,
        InputSourceService().http_client,
    ):
        assert count_upstream_request in client.event_hooks["request"]


@pytest.mark.asyncio
async def test_the_geocoder_counts_its_calls_without_trace_headers(monkeypatch):
    created = []

    class _Client:
        def __init__(self, **kwargs):
            created.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            return httpx.Response(200, json={"features": []})

    monkeypatch.setattr(geo_module.httpx, "AsyncClient", _Client)
    # Photon itself; through the B-API the call is one to an own service.
    use_settings(monkeypatch, Settings(_env_file=None, b_api_services="direct"))
    service = geo_module.GeocodingService()
    service.RATE_LIMIT_MS = 0

    await service.geocode_address("Weimar")

    assert created[0]["event_hooks"] is THIRD_PARTY_HOOKS


# ------------------------------------------------------------ tokens


def _answer(usage):
    body = {
        "choices": [
            {"message": {"content": '{"value": "Konzert"}'}, "finish_reason": "stop"}
        ]
    }
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def _llm(monkeypatch, response) -> LLMService:
    reset_provider_gates()
    cfg = _settings(llm_retry_delay=0)
    use_settings(monkeypatch, cfg)
    service = LLMService(llm_provider="b-api-openai")
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: response)
    )
    return service


def _tokens(kind) -> float:
    return _sample(
        "metadata_agent_llm_tokens_total", provider="b-api-openai", kind=kind
    )


@pytest.mark.asyncio
async def test_tokens_are_counted_from_the_answer(monkeypatch):
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 800},
    }
    service = _llm(monkeypatch, _answer(usage))
    before = {kind: _tokens(kind) for kind in ("prompt", "cached", "completion")}

    await service._call_llm([{"role": "user", "content": "x"}])

    assert _tokens("prompt") == before["prompt"] + 1000
    assert _tokens("cached") == before["cached"] + 800
    assert _tokens("completion") == before["completion"] + 50
    reset_provider_gates()


@pytest.mark.asyncio
async def test_tokens_written_to_the_prompt_cache_are_counted(monkeypatch):
    """A cache write costs 1.25 times the input price: without it the bill is a guess."""
    usage = {
        "prompt_tokens": 3885,
        "completion_tokens": 16,
        "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 3882},
    }
    service = _llm(monkeypatch, _answer(usage))
    before = {kind: _tokens(kind) for kind in ("cached", "cache_write")}

    await service._call_llm([{"role": "user", "content": "x"}])

    assert _tokens("cache_write") == before["cache_write"] + 3882
    assert _tokens("cached") == before["cached"]
    reset_provider_gates()


@pytest.mark.asyncio
async def test_an_answer_without_usage_counts_nothing_and_still_works(monkeypatch):
    service = _llm(monkeypatch, _answer(None))
    before = _tokens("prompt")

    await service._call_llm([{"role": "user", "content": "x"}])

    assert _tokens("prompt") == before
    reset_provider_gates()


@pytest.mark.asyncio
async def test_the_cache_primer_counts_its_tokens(monkeypatch):
    """The primer is cut off after 16 tokens on purpose; it still costs input."""
    truncated = httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 700, "completion_tokens": 16},
        },
    )
    service = _llm(monkeypatch, truncated)
    before = _tokens("prompt")

    await service._prime_text_cache("Ein Konzert in Weimar.", "de")

    assert _tokens("prompt") == before + 700
    reset_provider_gates()
