"""
/widget/info names no host: its URLs are relative (audit 14.11., S-43).

The answer used to build absolute URLs from the request - from the `Host`
header, that is: `Host: evil.example` came back as the address to load the
scripts from, and behind the B-API proxy the URLs named the internal host
without the proxy's path prefix. (Before that, audit OPS-01: http:// behind a
TLS proxy unless uvicorn trusted X-Forwarded-Proto.) Relative references need
neither header: resolved against the address /widget/info was called on, they
keep scheme, host and prefix of the caller's side.
"""

from urllib.parse import urljoin

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src import main

HEADERS = [
    {},
    {"Host": "evil.example"},
    {"Host": 'evil.example"><script src=//x></script>'},
    {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "fwd.example"},
]


def _widget_info(headers: dict, trusted: str = "*") -> dict:
    app = ProxyHeadersMiddleware(main.app, trusted_hosts=trusted)
    with TestClient(app) as client:
        response = client.get("/widget/info", headers=headers)
    assert response.status_code == 200
    return response.json()


def _strings(value) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) else []


def _urls(info) -> list[str]:
    return [
        info["dist_base_url"],
        *info["scripts"]["required"],
        *info["scripts"]["styles"],
        *info["examples"].values(),
        *info["example_data"].values(),
        *(variant["example_url"] for variant in info["variants"].values()),
    ]


@pytest.mark.parametrize("headers", HEADERS)
def test_no_header_of_the_request_shows_in_the_answer(headers):
    info = _widget_info(headers)

    assert info == _widget_info({})
    for text in _strings(info):
        assert "evil.example" not in text
        assert "fwd.example" not in text
        assert "testserver" not in text


def test_the_urls_are_relative_references():
    for url in _urls(_widget_info({})):
        assert "://" not in url and not url.startswith("/"), url


def test_resolved_against_the_endpoint_they_lead_to_the_files():
    info = _widget_info({})
    with TestClient(main.app) as client:
        for url in info["scripts"]["required"] + info["scripts"]["styles"]:
            path = urljoin("/widget/info", url)
            assert path.startswith("/widget/dist/")
            assert client.get(path).status_code == 200, path
        for url in info["examples"].values():
            assert client.get(urljoin("/widget/info", url)).status_code == 200, url


def test_they_keep_the_path_prefix_of_a_proxy():
    called_on = "https://wlo.example/api/v1/proxy/metadata-agent-canvas/widget/info"

    for url in _urls(_widget_info({})):
        resolved = urljoin(called_on, url)
        assert resolved.startswith(
            "https://wlo.example/api/v1/proxy/metadata-agent-canvas/widget/"
        ), resolved


def test_the_snippets_mark_where_the_address_of_the_service_goes():
    info = _widget_info({"Host": "evil.example"})

    for variant in info["variants"].values():
        snippet = variant["snippet"]
        assert '<script src="{AGENT_URL}/widget/dist/main.js" defer>' in snippet
        assert 'api-url="{AGENT_URL}"' in snippet
    assert "{AGENT_URL}" in info["description"]
