"""
One error shape for every failure, and the spec says so (audit API-03).

Three shapes reached clients: failure_response (detail + error with code and
request id), FastAPI's HTTPException (detail only) and the 422 of request
validation (a list under detail). The OpenAPI spec documented no error answer
at all, so generated clients knew none of them. `detail` stays as it was -
existing clients read it - and `error` is added everywhere.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.api.responses import failure_response


def _assert_error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert "detail" in body
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    assert body["error"]["message"]


def test_an_http_exception_carries_code_and_request_id():
    with TestClient(main.app) as client:
        response = client.post(
            "/extract-field",
            json={"text": "x", "field_id": "gibt:es_nicht", "schema_file": "core.json"},
        )

    _assert_error(response, 404, "NOT_FOUND")
    assert "gibt:es_nicht" in response.json()["detail"]


@pytest.mark.parametrize("brk", ["\n", "\r", "\r\n"])
def test_an_http_exception_logs_one_line(caplog, brk):
    """A detail with a line break must not forge a second log line (LOG_FORMAT=text)."""
    with TestClient(main.app) as client:
        response = client.post(
            "/extract-field",
            json={
                "text": "x",
                "field_id": f"gibt:es{brk}2026-10-18 ERROR gefaelscht",
                "schema_file": "core.json",
            },
        )

    assert response.status_code == 404
    lines = [r.getMessage() for r in caplog.records if r.name == "src.api.responses"]
    assert lines and not any("\n" in line or "\r" in line for line in lines), lines


def test_a_failure_response_logs_one_line(caplog):
    failure_response(502, "x", {"code": "X", "message": "a\rb\nc"})

    lines = [r.getMessage() for r in caplog.records if r.name == "src.api.responses"]
    assert lines and not any("\n" in line or "\r" in line for line in lines), lines


def test_a_validation_error_keeps_its_list_and_gains_the_error():
    with TestClient(main.app) as client:
        response = client.post("/extract-field", json={"text": "x"})

    _assert_error(response, 422, "VALIDATION_ERROR")
    assert isinstance(response.json()["detail"], list)


def test_an_unknown_route_is_a_404_in_the_same_shape():
    with TestClient(main.app) as client:
        response = client.get("/gibt-es-nicht")

    _assert_error(response, 404, "NOT_FOUND")


def test_a_body_over_the_limit_is_a_413_in_the_same_shape(monkeypatch):
    monkeypatch.setattr(deps.settings, "max_request_bytes", 10)

    with TestClient(main.app) as client:
        response = client.post("/generate", content=b"x" * 100)

    _assert_error(response, 413, "REQUEST_TOO_LARGE")


def test_every_operation_documents_the_error_shape():
    spec = main.app.openapi()
    ref = "#/components/schemas/ErrorResponse"

    assert "ErrorResponse" in spec["components"]["schemas"]
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            responses = operation["responses"]
            for status in ("4XX", "5XX"):
                schema = responses[status]["content"]["application/json"]["schema"]
                assert schema == {"$ref": ref}, (path, method, status)
            assert "HTTPValidationError" not in str(responses), (path, method)


def test_endpoints_acting_with_the_service_account_say_so():
    """Audit S-13: the accepted gaps are documented where an integrator reads."""
    paths = main.app.openapi()["paths"]

    for path in (
        "/upload",
        "/nodes",
        "/node",
        "/workflow/{node_id}",
        "/screenshot",
        # Read non-public nodes with node_id / node_url (audit 18.10. final, S-18).
        "/generate",
        "/detect-content-type",
        "/extract-field",
    ):
        assert "Service-Konto" in paths[path]["post"]["description"], path


def test_the_notes_name_what_the_service_account_reveals_and_accepts():
    """S-18: history with editor names; any authority as workflow receiver."""
    paths = main.app.openapi()["paths"]

    workflow = paths["/workflow/{node_id}"]["post"]["description"]
    assert "Benutzernamen" in workflow and "receiver" in workflow
    assert "workflow_receiver" in paths["/upload"]["post"]["description"]
    assert "nicht öffentliche" in paths["/generate"]["post"]["description"]


def test_every_documented_gap_names_a_real_operation():
    """A gap for a route that does not exist documents nothing (review 18.10.)."""
    from src.api.openapi import _ACCEPTED_GAPS

    paths = main.app.openapi()["paths"]

    missing = [key for key in _ACCEPTED_GAPS if key[1] not in paths.get(key[0], {})]
    assert not missing
