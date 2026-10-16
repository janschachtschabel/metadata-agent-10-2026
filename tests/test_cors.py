"""
Cross-origin access for an embedded widget.

A host page that loads the widget straight from the agent (no proxy in between)
calls the agent from its own origin; the browser hands the answers to the
widget only with CORS headers. test_widget_bundle_browser.py cannot show this:
Playwright's route.fulfill adds those headers by itself.
"""

from fastapi.testclient import TestClient

from src import main

HOST = "https://host.example"


def test_a_foreign_page_may_read_what_the_widget_loads():
    with TestClient(main.app) as client:
        response = client.get("/widget/info", headers={"Origin": HOST})

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") in ("*", HOST)


def test_a_preflight_for_a_widget_request_is_granted():
    with TestClient(main.app) as client:
        response = client.options(
            "/generate",
            headers={
                "Origin": HOST,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") in ("*", HOST)
    assert "POST" in response.headers.get("access-control-allow-methods", "")
