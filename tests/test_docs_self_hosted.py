"""
The API docs load nothing from third-party servers.

FastAPI's default pages pulled Swagger UI from cdn.jsdelivr.net, the favicon
from fastapi.tiangolo.com, and /redoc additionally Google Fonts — every visitor
handed their IP address to those hosts, and the pages failed without internet.
Swagger UI now ships with the agent (src/static/docs); /redoc is gone.
"""

import re

from fastapi.testclient import TestClient

from src import main


def _client():
    return TestClient(main.app)


def test_the_docs_page_loads_only_from_the_agent():
    html = _client().get("/docs").text

    sources = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert sources, "the page should reference its assets"
    assert all(not s.startswith(("http://", "https://", "//")) for s in sources), (
        sources
    )


def test_the_swagger_validator_badge_is_off():
    # Swagger UI otherwise asks validator.swagger.io about the spec.
    assert '"validatorUrl": null' in _client().get("/docs").text


def test_the_shipped_assets_are_served():
    client = _client()
    for path in (
        "/docs-assets/swagger-ui-bundle.js",
        "/docs-assets/swagger-ui.css",
        "/docs-assets/favicon-32x32.png",
    ):
        assert client.get(path).status_code == 200, path


def test_redoc_is_gone():
    assert _client().get("/redoc").status_code == 404


def test_behind_a_path_prefix_the_assets_keep_the_prefix():
    html = (
        TestClient(main.app, root_path="/api/v1/proxy/metadata-agent").get("/docs").text
    )

    assert 'src="/api/v1/proxy/metadata-agent/docs-assets/swagger-ui-bundle.js"' in html
    assert "/api/v1/proxy/metadata-agent/openapi.json" in html
