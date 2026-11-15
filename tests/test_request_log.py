"""
Every request leaves one log line: method, path, status and duration.

It replaces uvicorn's access log (the developer guide for services, 2.3): the
line carries the ids of the request, so it can be matched with the lines of
other services, and never the query string or a header - a key or a search
term must not end up in the log.
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from src import main
from src.utils.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def _restore_logging():
    """configure_logging changes process-wide state; put it back afterwards."""
    names = ["", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"]
    saved = {
        n: (
            logging.getLogger(n).handlers[:],
            logging.getLogger(n).level,
            logging.getLogger(n).propagate,
        )
        for n in names
    }
    yield
    for n, (handlers, level, propagate) in saved.items():
        logger = logging.getLogger(n)
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate


def _records(out: str) -> list[dict]:
    """The agent's JSON lines - without those of the test client's HTTP library."""
    records = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return [r for r in records if r["logger"] != "httpx2"]


def _request_lines(out: str) -> list[dict]:
    return [r for r in _records(out) if "duration_ms" in r]


def test_a_request_leaves_one_line_with_the_ids_of_the_response(capsys):
    configure_logging("INFO", "json")

    with TestClient(main.app) as client:
        capsys.readouterr()
        response = client.get("/info/schemata")

    (line,) = _request_lines(capsys.readouterr().out)
    assert (line["method"], line["path"]) == ("GET", "/info/schemata")
    assert line["status"] == 200
    assert line["duration_ms"] >= 0
    assert line["request_id"] == response.headers["x-request-id"]
    assert line["trace_id"] and line["span_id"]


def test_query_string_and_headers_stay_out_of_the_line(capsys):
    configure_logging("INFO", "json")

    with TestClient(main.app) as client:
        capsys.readouterr()
        client.get(
            "/no-such-path?token=geheim-123",
            headers={"Authorization": "Bearer geheim-456"},
        )

    out = capsys.readouterr().out
    (line,) = _request_lines(out)
    assert (line["path"], line["status"]) == ("/no-such-path", 404)
    assert "geheim" not in json.dumps(_records(out))


def test_a_path_cannot_forge_a_second_log_line(capsys):
    # Starlette decodes the path: %0A would be a line break in text mode.
    configure_logging("INFO", "text")

    with TestClient(main.app) as client:
        capsys.readouterr()
        client.get("/x%0AGET /forged 200 1 ms")

    lines = [line for line in capsys.readouterr().out.splitlines() if " ms" in line]
    assert len(lines) == 1 and lines[0].startswith("GET /x%0A")


def test_probes_stay_out_of_the_log(capsys):
    configure_logging("INFO", "json")

    with TestClient(main.app) as client:
        capsys.readouterr()
        client.get("/health")

    assert _request_lines(capsys.readouterr().out) == []


def test_a_very_long_path_is_cut_in_the_line(capsys):
    """Audit 13.11., L-25: a 60 kB path made a log line of twice that size."""
    configure_logging("INFO", "json")

    with TestClient(main.app) as client:
        capsys.readouterr()
        client.get("/" + "a" * 5000)

    (line,) = _request_lines(capsys.readouterr().out)
    assert len(line["path"]) <= 2001 and line["path"].endswith("…")
    assert len(line["message"]) < 2100
