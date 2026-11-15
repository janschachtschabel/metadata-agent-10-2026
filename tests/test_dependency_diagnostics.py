"""
/debug/dependencies: can this instance reach what it needs, and is its key taken?

Built for what production showed on 2026-10-14: /health answered 200 while
every LLM call got a 403 and every URL extraction a connect timeout. The check
lists the models with the key the LLM calls of the request would carry - no
tokens, and refused exactly like a generation would be. Only with
METADATA_AGENT_DEBUG_ENDPOINTS; never shows a key.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.config import Settings
from src.services import diagnostics
from src.services.llm_auth import forwarded_authorization
from conftest import use_settings

BEARER = "Bearer 0123456789abcdef0123456789abcdef"
EXTRACTOR = "http://bildungs-api-services-full-text.b-api"


def _settings(**overrides) -> Settings:
    defaults = {"b_api_key": "own-key", "text_extraction_api_url": EXTRACTOR}
    return Settings(_env_file=None, **{**defaults, **overrides})


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _models_calls(seen):
    return [r for r in seen if r.url.path.endswith("/models")]


@pytest.mark.asyncio
async def test_a_refused_key_shows_up_without_a_generation():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(403, text="AuthorizationDeniedException")
        return httpx.Response(404)

    with forwarded_authorization(BEARER):
        report = await diagnostics.check_dependencies(_settings(), _client(handler))

    llm = report["checks"]["llm"]
    assert llm["status"] == 403
    assert llm["authorized"] is False
    assert llm["auth_source"] == "forwarded"
    (call,) = _models_calls(seen)
    assert call.method == "GET"
    assert call.headers["authorization"] == BEARER
    assert "x-api-key" not in call.headers
    assert "0123456789abcdef" not in str(report)
    assert "own-key" not in str(report)


@pytest.mark.asyncio
async def test_an_unreachable_service_is_named_and_the_rest_still_checked():
    def handler(request):
        if request.url.host == "bildungs-api-services-full-text.b-api":
            raise httpx.ConnectTimeout("")
        return httpx.Response(200, json={"data": []})

    report = await diagnostics.check_dependencies(_settings(), _client(handler))

    text_extraction = report["checks"]["text_extraction"]
    assert text_extraction["reachable"] is False
    assert text_extraction["error"] == "ConnectTimeout"
    assert text_extraction["url"] == EXTRACTOR
    assert report["checks"]["repository"]["reachable"] is True
    assert report["checks"]["llm"]["authorized"] is True
    assert report["checks"]["llm"]["auth_source"] == "static"


@pytest.mark.asyncio
async def test_any_http_answer_counts_as_reachable():
    report = await diagnostics.check_dependencies(
        _settings(), _client(lambda request: httpx.Response(404))
    )

    assert report["checks"]["repository"] == {
        "url": report["checks"]["repository"]["url"],
        "reachable": True,
        "status": 404,
        "ms": report["checks"]["repository"]["ms"],
    }


@pytest.mark.asyncio
async def test_without_a_key_no_llm_call_is_made():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    report = await diagnostics.check_dependencies(
        _settings(b_api_auth_mode="forward"), _client(handler)
    )

    assert report["checks"]["llm"]["error"] == "LLM_AUTH_MISSING"
    assert _models_calls(seen) == []


@pytest.mark.asyncio
async def test_the_report_names_version_mode_and_config_warnings():
    report = await diagnostics.check_dependencies(
        _settings(b_api_auth_mode="static", b_api_key=""),
        _client(lambda request: httpx.Response(200)),
    )

    assert report["version"] == Settings(_env_file=None).app_version
    assert report["b_api_auth_mode"] == "static"
    assert any("B_API_KEY" in w for w in report["config_warnings"])


# ------------------------------------------------------------------ endpoint


def test_the_endpoint_does_not_exist_unless_switched_on(monkeypatch):
    use_settings(monkeypatch, _settings())

    with TestClient(main.app) as client:
        assert client.get("/debug/dependencies").status_code == 404


def test_switched_on_the_endpoint_reports(monkeypatch):
    use_settings(monkeypatch, _settings(debug_endpoints=True))

    async def fake_check(settings, client=None):
        return {"checks": {"llm": {"authorized": True}}}

    monkeypatch.setattr(deps, "check_dependencies", fake_check)

    with TestClient(main.app) as client:
        response = client.get("/debug/dependencies")

    assert response.status_code == 200
    assert response.json()["checks"]["llm"]["authorized"] is True


# ------------------------------------------------------------------ router


def _router_lists(*routes):
    def handler(request):
        if request.url.path.endswith("/router/models"):
            return httpx.Response(200, json={"data": [{"id": r} for r in routes]})
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "routes, listed", [(["metadata-agent"], True), (["other"], False)]
)
async def test_a_route_name_is_looked_up_in_the_routers_list(routes, listed):
    settings = _settings(
        llm_provider="b-api-router",
        b_api_router_model="metadata-agent",
        llm_request_style="classic",
    )

    report = await diagnostics.check_dependencies(
        settings, _client(_router_lists(*routes))
    )

    llm = report["checks"]["llm"]
    assert llm["model"] == "metadata-agent"
    assert llm["route_listed"] is listed


@pytest.mark.asyncio
async def test_provider_slash_model_is_not_expected_in_the_list():
    # The router never lists the provider/model pattern; it works without a route.
    settings = _settings(llm_provider="b-api-router")

    report = await diagnostics.check_dependencies(settings, _client(_router_lists("x")))

    assert "route_listed" not in report["checks"]["llm"]


# ------------------------------------------------- services through the B-API
#
# Audit 13.11., OPS-19: text extraction and geocoder go through the B-API with
# the key of the request. The check asked without a key and called the 401
# "reachable" - while every URL extraction ended with 502. The geocoder was
# not checked at all.

B_API = "https://b-api.staging.openeduhub.net"


def _through_the_b_api(**overrides) -> Settings:
    return Settings(_env_file=None, **{"b_api_key": "own-key", **overrides})


def _answers(seen, statuses):
    def handler(request):
        seen.append(request)
        for part, status in statuses.items():
            if part in request.url.path:
                return httpx.Response(status, json={"data": [], "features": []})
        return httpx.Response(200, json={"data": [], "features": []})

    return handler


@pytest.mark.asyncio
async def test_services_behind_the_b_api_are_asked_with_the_key_of_the_request():
    seen = []

    with forwarded_authorization(BEARER):
        report = await diagnostics.check_dependencies(
            _through_the_b_api(), _client(_answers(seen, {}))
        )

    checks = report["checks"]
    assert checks["text_extraction"]["authorized"] is True
    assert checks["geocoder"]["authorized"] is True
    by_path = {request.url.path: request for request in seen}
    extraction = by_path["/api/v1/proxy/text-extraction/metrics"]
    geocoder = by_path["/api/v1/proxy/photon_geocoding/api"]
    for call in (extraction, geocoder):
        assert call.method == "GET"
        assert call.headers["authorization"] == BEARER
        assert "x-api-key" not in call.headers
    assert "0123456789abcdef" not in str(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_a_key_without_the_proxy_rights_shows_up(status):
    seen = []
    statuses = {"text-extraction": status, "photon_geocoding": status}

    report = await diagnostics.check_dependencies(
        _through_the_b_api(), _client(_answers(seen, statuses))
    )

    for name in ("text_extraction", "geocoder"):
        check = report["checks"][name]
        assert (check["reachable"], check["status"]) == (True, status)
        assert check["authorized"] is False
        assert check["auth_source"] == "static"
    assert "own-key" not in str(report)


@pytest.mark.asyncio
async def test_a_service_of_its_own_gets_no_key_and_photon_is_not_called():
    seen = []
    settings = _settings(b_api_services="direct")

    with forwarded_authorization(BEARER):
        report = await diagnostics.check_dependencies(
            settings, _client(_answers(seen, {}))
        )

    extraction = report["checks"]["text_extraction"]
    assert extraction["reachable"] is True and "authorized" not in extraction
    (call,) = [r for r in seen if r.url.host == "bildungs-api-services-full-text.b-api"]
    assert "authorization" not in call.headers and "x-api-key" not in call.headers
    # Photon itself is a third party: no call from here, least of all one with
    # tracing headers.
    assert "skipped" in report["checks"]["geocoder"]
    assert not [r for r in seen if r.url.host == "photon.komoot.io"]


@pytest.mark.asyncio
async def test_no_text_extraction_at_all_is_said_so():
    seen = []
    settings = Settings(
        _env_file=None,
        llm_provider="openai",
        repository_url="https://repo.example/edu-sharing/rest",
    )

    report = await diagnostics.check_dependencies(settings, _client(_answers(seen, {})))

    assert report["checks"]["text_extraction"] == {"configured": False}


@pytest.mark.asyncio
async def test_without_a_key_the_services_behind_the_b_api_are_not_called():
    seen = []

    report = await diagnostics.check_dependencies(
        _through_the_b_api(b_api_key=""), _client(_answers(seen, {}))
    )

    for name in ("text_extraction", "geocoder"):
        assert report["checks"][name]["error"] == "LLM_AUTH_MISSING"
    assert not [r for r in seen if "/proxy/" in r.url.path]
