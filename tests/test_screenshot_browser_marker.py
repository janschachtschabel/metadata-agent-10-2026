"""
The API refuses requests of its own screenshot browser (audit 18.10. final, S-15).

A captured page could make Chromium send requests - through redirect hops the
URL check never saw - to the agent's API on loopback. Chromium marks every
request; the API answers a marked one with 403 before any endpoint runs.
Callers without the marker (2.x widget, edu-sharing proxy, the Docker
HEALTHCHECK, local development on 127.0.0.1) are not affected.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.utils.security_headers import (
    SCREENSHOT_BROWSER_HEADER,
    SCREENSHOT_BROWSER_TOKEN,
)

NODE_ID = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"


@pytest.fixture
def client():
    with TestClient(main.app) as client:
        yield client


@pytest.mark.parametrize("value", [SCREENSHOT_BROWSER_TOKEN, "anything"])
def test_a_marked_request_is_refused_before_any_endpoint(client, value):
    response = client.post(
        f"/workflow/{NODE_ID}",
        content='{"steps": ["150_PUBLISH_IN_SEARCH"]}',
        headers={"Content-Type": "text/plain", SCREENSHOT_BROWSER_HEADER: value},
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "SCREENSHOT_BROWSER_REFUSED"
    assert body["detail"] == body["error"]["message"]
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


def test_unmarked_callers_are_not_affected(client):
    """The Docker HEALTHCHECK and every 2.x client send no marker."""
    assert client.get("/health").status_code == 200
    response = client.post(
        "/validate",
        content='{"metadata": {"cclom:title": "Titel"}}',
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 200, response.text


def test_the_internal_port_refuses_the_screenshot_browser_too():
    """
    Audit 13.11., S-23: only port 8000 refused the marker. Through DNS
    rebinding a captured page could have read /metrics, /health and /ready of
    the internal port on loopback - no NetworkPolicy covers loopback.
    """
    from src.api import deps
    from src.infra import create_internal_app

    with TestClient(create_internal_app(lambda: deps.settings)) as internal:
        for path in ("/metrics", "/health", "/ready"):
            marked = internal.get(
                path, headers={SCREENSHOT_BROWSER_HEADER: SCREENSHOT_BROWSER_TOKEN}
            )
            assert marked.status_code == 403, path
        # Probes and Prometheus send no marker.
        assert internal.get("/metrics").status_code == 200
