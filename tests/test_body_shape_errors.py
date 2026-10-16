"""
A body that is not a JSON object is the caller's mistake: 400, never 500.

/nodes, /workflow and /upload/verify already said so; /validate,
/export/markdown and /upload crashed on a list, a number or invalid UTF-8.
"""

import pytest
from fastapi.testclient import TestClient

from src import main

ENDPOINTS = ["/validate", "/export/markdown", "/upload"]
BODIES = [b"[1, 2]", b"42", b'"text"', b"\xff\xfe{}"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "get_repository_service", lambda: object())
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize("body", BODIES)
def test_a_body_that_is_no_json_object_is_a_400(client, path, body):
    response = client.post(
        path, content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 400, response.text
