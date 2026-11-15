"""
The small findings of list S-N in the API layer (audit 14.11.).

- An `x-request-id` of 65 to 128 characters went on to the agent's services
  unchanged while answer and log used a new id: nothing matched.
- An error answer was logged as ERROR whatever its status - also the 422 of a
  caller and the 503 that turns a generation away on purpose.
- A node without any text ended as 502 "Failed to fetch input: ValueError".
- /detect-content-type and /extract-field cut the text to 150,000 characters
  without saying so.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.api.input_resolver import MAX_TEXT_LENGTH
from src.api.responses import failure_response
from src.metrics import GENERATIONS
from src.services.input_source_service import InputData, NodeWithoutText
from src.utils import request_id

NODE = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"


# ------------------------------------------------------------ x-request-id


@pytest.mark.parametrize("length", [65, 100, 128])
def test_a_request_id_too_long_for_the_answer_is_not_passed_on_either(length):
    with request_id.traced({"x-request-id": "a" * length}) as own:
        passed_on = request_id.trace_headers()["x-request-id"]

    assert own != "a" * length
    assert passed_on == own


@pytest.mark.parametrize("value", ["abc-123", "a" * 64])
def test_a_request_id_that_fits_is_the_id_everywhere(value):
    with request_id.traced({"x-request-id": value}) as own:
        passed_on = request_id.trace_headers()["x-request-id"]

    assert own == passed_on == value


def test_a_client_trace_id_still_goes_on_next_to_the_callers_request_id():
    headers = {"x-client-trace-id": "client-1", "x-request-id": "req-1"}

    with request_id.traced(headers) as own:
        passed_on = request_id.trace_headers()

    assert own == "client-1"
    assert passed_on["x-request-id"] == "req-1"
    assert passed_on["x-client-trace-id"] == "client-1"


# ------------------------------------------------------------ log level


@pytest.mark.parametrize(
    "status, level",
    [(400, "INFO"), (404, "INFO"), (422, "INFO"), (500, "ERROR"), (502, "ERROR")],
)
def test_an_error_answer_is_logged_by_whose_fault_it_is(caplog, status, level):
    with caplog.at_level(logging.INFO, logger="src.api.responses"):
        failure_response(status, "Meldung", {"code": "X", "message": "M"})

    (record,) = caplog.records
    assert record.levelname == level
    assert f"{status} X: M" in record.getMessage()


def test_a_generation_turned_away_on_purpose_is_a_warning(monkeypatch, caplog):
    monkeypatch.setattr(deps.settings, "max_concurrent_generations", 0)
    refused = GENERATIONS.labels("refused")._value.get()
    failed = GENERATIONS.labels("failed")._value.get()

    with caplog.at_level(logging.INFO, logger="src.api.responses"):
        with TestClient(main.app) as client:
            response = client.post("/generate", json={"text": "Ein Text"})

    assert response.status_code == 503
    (record,) = [r for r in caplog.records if "TOO_MANY_GENERATIONS" in r.getMessage()]
    assert record.levelname == "WARNING"
    # Counted as refused, so the alert on 5xx answers can leave it out.
    assert GENERATIONS.labels("refused")._value.get() == refused + 1
    assert GENERATIONS.labels("failed")._value.get() == failed


# ------------------------------------------------------------ node without text


class _EmptyNode:
    """The input service's answer for a node that holds nothing to read."""

    async def fetch_from_node_id(self, node_id, **kwargs):
        raise NodeWithoutText(f"No text content available for node {node_id}")

    async def fetch_from_node_url(self, node_id, **kwargs):
        raise NodeWithoutText(
            f"No stored fulltext and no URL available for node {node_id}"
        )


@pytest.mark.parametrize("source", ["node_id", "node_url"])
@pytest.mark.parametrize(
    "path, extra",
    [
        ("/generate", {}),
        ("/detect-content-type", {}),
        ("/extract-field", {"schema_file": "event.json", "field_id": "cclom:title"}),
    ],
)
def test_a_node_without_text_is_a_422_that_says_so(monkeypatch, source, path, extra):
    monkeypatch.setattr(deps, "get_input_source_service", lambda: _EmptyNode())

    with TestClient(main.app) as client:
        response = client.post(
            path, json={"input_source": source, "node_id": NODE, **extra}
        )

    assert response.status_code == 422, response.text
    assert "keinen Text" in response.json()["detail"]
    assert "ValueError" not in response.text


class _BrokenAnswer:
    async def fetch_from_node_id(self, node_id, **kwargs):
        import json

        return json.loads("kein JSON")  # a ValueError of another kind


def test_another_value_error_of_the_source_stays_a_502(monkeypatch):
    monkeypatch.setattr(deps, "get_input_source_service", lambda: _BrokenAnswer())

    with TestClient(main.app) as client:
        response = client.post(
            "/generate", json={"input_source": "node_id", "node_id": NODE}
        )

    assert response.status_code == 502


# ------------------------------------------------------------ cut text

LONG = "Ein langer Text." * 10_625  # 170,000 characters
CUT = "Quelltext auf 150.000 Zeichen gekürzt (Original: 170.000 Zeichen)."


class _Detector:
    llm_service = None

    def __init__(self):
        self.texts = []

    async def detect_schema(self, text, context, version, language):
        self.texts.append(text)
        return {"schema_file": "event.json", "confidence": "high", "method": "llm"}


class _Extractor:
    provider = model = "fake"

    def __init__(self):
        self.texts = []

    async def extract_field(self, field, text, existing_value, language):
        self.texts.append(text)
        return field["id"], "Titel", None

    async def close(self):
        pass


def test_content_type_detection_says_when_it_cut_the_text(monkeypatch):
    detector = _Detector()
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: detector)

    with TestClient(main.app) as client:
        long = client.post("/detect-content-type", json={"text": LONG})
        short = client.post("/detect-content-type", json={"text": "Ein Text."})

    assert long.status_code == short.status_code == 200
    assert len(detector.texts[0]) == MAX_TEXT_LENGTH
    assert long.json()["warnings"] == [CUT]
    assert short.json()["warnings"] == []


def test_field_extraction_says_when_it_cut_the_text(monkeypatch):
    extractor = _Extractor()
    monkeypatch.setattr(deps, "get_llm_service", lambda **kw: extractor)
    field = {"schema_file": "event.json", "field_id": "cclom:title"}

    with TestClient(main.app) as client:
        long = client.post("/extract-field", json={"text": LONG, **field})
        short = client.post("/extract-field", json={"text": "Ein Text.", **field})

    assert long.status_code == short.status_code == 200, long.text
    assert len(extractor.texts[0]) == MAX_TEXT_LENGTH
    assert long.json()["processing"]["warnings"] == [CUT]
    assert short.json()["processing"]["warnings"] == []


def test_the_fake_node_matches_the_service():
    # Guards the two fakes above: the service raises a bare ValueError there.
    assert InputData(text="x").warnings == []
