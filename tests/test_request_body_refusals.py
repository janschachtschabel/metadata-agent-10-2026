"""
One answer per kind of unusable body, on every operation that takes one
(audit 14.11., S-32, S-33, S-42, L-37 and the small items of list S-N).

- NaN and Infinity are no JSON: 400. On the four endpoints FastAPI validates
  (/detect-content-type, /extract-field, /node, /screenshot) they ended as 500,
  502 or a silent null.
- A JSON value that is no object: 400 everywhere (it was 422 on those four).
- JSON nested deeper than the parser goes: 400, not 500.
- The 422 of a body its model refuses names place, message and kind of each
  problem and repeats no value of the request; a body it cannot repeat (invalid
  UTF-8, deep nesting) is no 500.
- `"_schema": null` is "no legacy block", not a 500.
"""

import json

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps

NODE = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"

# Every operation with a request body.
BODY_OPERATIONS = [
    "/generate",
    "/detect-content-type",
    "/extract-field",
    "/validate",
    "/export/markdown",
    "/upload",
    "/nodes",
    "/node",
    f"/workflow/{NODE}",
    f"/upload/verify/{NODE}",
    "/screenshot",
]
MODEL_BOUND = ["/detect-content-type", "/extract-field", "/node", "/screenshot"]


def test_the_list_names_every_operation_with_a_body():
    spec = main.app.openapi()
    with_body = {
        path.replace("{node_id}", NODE)
        for path, operations in spec["paths"].items()
        for operation in operations.values()
        if "requestBody" in operation
    }

    assert with_body == set(BODY_OPERATIONS)


def _not_reached(*args, **kwargs):
    raise AssertionError("the body was refused too late: a service was asked for")


@pytest.fixture
def client(monkeypatch):
    # Past the 503 of the write endpoints; a refused body reaches no service.
    monkeypatch.setattr(deps, "get_repository_service", lambda: object())
    for name in (
        "get_llm_service",
        "get_screenshot_service",
        "get_input_source_service",
    ):
        monkeypatch.setattr(deps, name, _not_reached)
    with TestClient(main.app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def refusing_client(client, monkeypatch):
    monkeypatch.setattr(deps, "get_metadata_service", _not_reached)
    return client


def _post(client, path, body: bytes, content_type="application/json"):
    return client.post(path, content=body, headers={"content-type": content_type})


def _assert_error(response, status):
    assert response.status_code == status, response.text
    answer = response.json()
    assert answer["error"]["request_id"]
    return answer


@pytest.mark.parametrize("path", BODY_OPERATIONS)
@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nan_and_infinity_are_a_400(refusing_client, path, constant):
    body = '{"text": "abc", "metadata": {"cclom:title": %s}, "x": %s}' % (
        constant,
        constant,
    )

    answer = _assert_error(_post(refusing_client, path, body.encode()), 400)

    assert "kein gültiger JSON-Wert" in answer["detail"]


@pytest.mark.parametrize("path", BODY_OPERATIONS)
@pytest.mark.parametrize("body", [b"[1, 2]", b"42", b'"text"', b"null"])
def test_a_json_value_that_is_no_object_is_a_400(refusing_client, path, body):
    answer = _assert_error(_post(refusing_client, path, body), 400)

    assert "JSON-Objekt" in answer["detail"]


@pytest.mark.parametrize("path", BODY_OPERATIONS)
def test_json_nested_too_deep_is_a_400(refusing_client, path):
    depth = 100_000
    body = '{"metadata": {"a": %s}, "text": "x"}' % ("[" * depth + "]" * depth)

    _assert_error(_post(refusing_client, path, body.encode()), 400)


@pytest.mark.parametrize("path", BODY_OPERATIONS)
def test_invalid_utf8_is_a_400(refusing_client, path):
    _assert_error(_post(refusing_client, path, b'{"text": "\xff\xfe"}'), 400)


# ------------------------------------------------------------ the 422

SECRET = "GEHEIMER-QUELLTEXT"


@pytest.mark.parametrize(
    "path, body",
    [
        ("/extract-field", {"text": SECRET, "context": "default"}),
        ("/detect-content-type", {"text": SECRET, "input_source": SECRET}),
        ("/detect-content-type", {"text": SECRET, "version": 5}),
        ("/node", {"metadata": SECRET}),
        ("/node", {"x": SECRET, "cclom:title": "Titel"}),
        ("/screenshot", {"method": SECRET, "note": SECRET}),
        ("/screenshot", {"url": "https://example.org/", "width": 1, "x": SECRET}),
    ],
)
def test_the_422_repeats_no_value_of_the_request(refusing_client, path, body):
    response = refusing_client.post(path, json=body)

    answer = _assert_error(response, 422)
    assert answer["error"]["code"] == "VALIDATION_ERROR"
    assert SECRET not in response.text
    assert answer["detail"]
    for problem in answer["detail"]:
        assert set(problem) == {"loc", "msg", "type"}
        assert problem["loc"][0] == "body"


def test_the_422_does_not_grow_with_the_body(refusing_client):
    response = refusing_client.post("/extract-field", json={"text": "A" * 200_000})

    assert response.status_code == 422
    assert len(response.content) < 2_000


@pytest.mark.parametrize("path", MODEL_BOUND)
def test_a_body_that_cannot_be_repeated_is_no_500(refusing_client, path):
    response = _post(refusing_client, path, b"\xff\xfe\xfa geheim", "text/plain")

    _assert_error(response, 422)


def test_deep_nesting_in_a_refused_body_is_no_500(refusing_client):
    depth = 3000
    nested = "[" * depth + "]" * depth
    body = '{"text": "x", "existing_metadata": {"a": %s}}' % nested

    response = _post(refusing_client, "/extract-field", body.encode())

    assert response.status_code in (400, 422), response.text
    assert "error" in response.json()


# ------------------------------------------------------------ "_schema": null


@pytest.mark.parametrize("path", ["/validate", "/export/markdown"])
@pytest.mark.parametrize("nested", [True, False])
def test_a_null_legacy_schema_block_is_no_block(client, path, nested):
    record = {"cclom:title": "Titel", "metadataset": "event.json", "_schema": None}

    response = client.post(path, json={"metadata": record} if nested else record)

    assert response.status_code == 200, response.text
    assert response.json()["schema_used"] == "event.json"


def test_an_invalid_body_logs_no_content(client, caplog):
    with caplog.at_level("INFO"):
        _post(client, "/screenshot", json.dumps({"url": 5, "note": SECRET}).encode())

    assert SECRET not in caplog.text
