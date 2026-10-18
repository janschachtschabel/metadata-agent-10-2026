"""
/generate with a JSON body that is no object answers 400 like the other endpoints.

A list, number or string body reached GenerateRequest(**data) and came back
as 422 with a Python-internal message ("argument after ** must be a mapping").
"""

import pytest
from fastapi.testclient import TestClient

from src import main


@pytest.mark.parametrize("body", [b"[1, 2]", b"42", b'"text"', b"null"])
def test_a_json_body_that_is_no_object_is_a_400(body):
    client = TestClient(main.app, raise_server_exceptions=False)

    response = client.post(
        "/generate", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 400, response.text
    assert "mapping" not in response.text
    assert "JSON-Objekt" in response.text
