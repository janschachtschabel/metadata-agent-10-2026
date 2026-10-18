"""
Behind a TLS proxy the widget's embedding URLs use https (audit OPS-01).

uvicorn honours X-Forwarded-Proto only from FORWARDED_ALLOW_IPS; the Helm chart
sets it (forwardedAllowIps). This shows the app side: with uvicorn's proxy
middleware trusting the proxy, /widget/info builds https URLs.
"""

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from src import main


def _widget_info(headers: dict, trusted: str) -> dict:
    app = ProxyHeadersMiddleware(main.app, trusted_hosts=trusted)
    with TestClient(app) as client:
        response = client.get("/widget/info", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_a_trusted_tls_proxy_yields_https_urls():
    info = _widget_info({"X-Forwarded-Proto": "https"}, trusted="*")

    assert all(value.startswith("https://") for value in _urls(info)), _urls(info)


def test_an_untrusted_client_cannot_switch_the_scheme():
    info = _widget_info({"X-Forwarded-Proto": "https"}, trusted="10.1.2.3")

    assert all(value.startswith("http://") for value in _urls(info)), _urls(info)


def _urls(info) -> list[str]:
    found = []

    def walk(value):
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif (
            isinstance(value, str)
            and value.startswith("http")
            and "testserver" in value
        ):
            found.append(value)

    walk(info)
    assert found, "no URLs of the agent in /widget/info"
    return found
