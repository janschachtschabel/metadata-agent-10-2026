"""
An unexpected exception answers like every other error (audit API-02).

Without a handler, Starlette's outermost ServerErrorMiddleware answered with a
plain-text 500 - outside CORS and the request id: an embedding page could not
read the answer at all, and nobody could quote an id to find the log line.
"""

import re

from fastapi.testclient import TestClient

from src import main
from src.api.routers import operations

HOST = "https://host.example"


def _boom(settings, **options):
    raise RuntimeError("secret internal detail")


def test_an_unexpected_exception_answers_500_with_id_and_cors(monkeypatch):
    monkeypatch.setattr(operations, "health_payload", _boom)

    with TestClient(main.app, raise_server_exceptions=False) as client:
        response = client.get("/health", headers={"Origin": HOST})

    assert response.status_code == 500
    body = response.json()
    request_id = response.headers["x-request-id"]
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["request_id"] == request_id
    assert response.headers.get("access-control-allow-origin") in ("*", HOST)
    # The cause stays in the log, the client gets the id.
    assert "secret internal detail" not in response.text
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)


def test_the_failure_is_logged_under_the_request_id(monkeypatch, caplog):
    monkeypatch.setattr(operations, "health_payload", _boom)

    with TestClient(main.app, raise_server_exceptions=False) as client:
        response = client.get("/health")

    request_id = response.headers["x-request-id"]
    assert any(
        request_id in r.getMessage() and "RuntimeError" in r.getMessage()
        for r in caplog.records
    )
