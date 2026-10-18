"""
A value the editor typed is not overwritten by the LLM (review of audit U-06).

The widget sends `_origins` with every re-extraction - and since 3.0.0 also
with a content-type switch - so that what a person entered stays. The merge
marked every extracted value as "ai" and wrote it over the existing one: a typed
title could be replaced by the page title, and even an unchanged one lost its
"user" mark.
"""

from fastapi.testclient import TestClient

from src import main

from test_generate_failures import _Gateway, stack  # noqa: F401 - fixture


def test_a_typed_value_and_its_origin_survive_a_new_run(stack):  # noqa: F811
    stack(_Gateway(content='{"value": "Titel aus der Seite"}'))

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={
                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                "schema_file": "event.json",
                "enable_geocoding": False,
                "existing_metadata": {
                    "cclom:title": "Mein Titel",
                    "_origins": {"cclom:title": "user"},
                },
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cclom:title"] == "Mein Titel"
    assert body["_origins"]["cclom:title"] == "user"


def test_an_ai_value_is_still_replaced(stack):  # noqa: F811
    stack(_Gateway(content='{"value": "Titel aus der Seite"}'))

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={
                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                "schema_file": "event.json",
                "enable_geocoding": False,
                "existing_metadata": {
                    "cclom:title": "Alter KI-Titel",
                    "_origins": {"cclom:title": "ai"},
                },
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["cclom:title"] == "Titel aus der Seite"
