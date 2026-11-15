"""
Every response carries a request id, and every error body repeats it.

Someone who sees "KI-Erschließung fehlgeschlagen" can quote the id, and the one
log line written for that failure carries the same id - the technical cause
stays in the log, the browser gets a readable message and the id. An id that
a proxy in front already assigned is kept, as long as it is a plain token.
"""

import re

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services.input_source_service import TextExtractionError
from src.services.llm_errors import LLMAuthForbidden
from src.utils.request_id import (
    TRACE_EVENT_HOOKS,
    current_trace_ids,
    propagate_trace,
    traced,
)

# Without an id from outside, the request id is the new B3 trace id.
GENERATED = re.compile(r"[0-9a-f]{32}")
SPAN = re.compile(r"[0-9a-f]{16}")


def test_every_response_gets_its_own_id():
    with TestClient(main.app) as client:
        first = client.get("/health").headers["x-request-id"]
        second = client.get("/health").headers["x-request-id"]

    assert GENERATED.fullmatch(first)
    assert first != second


def test_an_id_from_the_proxy_is_kept():
    with TestClient(main.app) as client:
        response = client.get("/health", headers={"X-Request-ID": "bapi-7f3c.9_A"})

    assert response.headers["x-request-id"] == "bapi-7f3c.9_A"


@pytest.mark.parametrize("incoming", ["two words", "x" * 65, "<script>", "a;b"])
def test_anything_but_a_plain_token_is_replaced(incoming):
    with TestClient(main.app) as client:
        response = client.get("/health", headers={"X-Request-ID": incoming})

    assert GENERATED.fullmatch(response.headers["x-request-id"])


def test_browsers_on_other_origins_may_read_the_id():
    with TestClient(main.app) as client:
        response = client.get("/health", headers={"Origin": "https://example.org"})

    exposed = response.headers.get("access-control-expose-headers", "").lower()
    assert "x-request-id" in exposed


class _FailingInput:
    async def fetch_from_url(self, *args, **kwargs):
        raise TextExtractionError(
            "TEXT_EXTRACTION_CONNECT_TIMEOUT", "Textextraktion nicht erreichbar", 504
        )


class _RefusedLLM:
    provider, model = "b-api-openai", "gpt-6-luna"

    async def extract_field(self, **kwargs):
        raise LLMAuthForbidden("Der KI-Dienst hat den Zugriff verweigert (403).")

    async def close(self):
        pass


class _FailedGeneration:
    llm_service = None

    async def generate_metadata(self, **kwargs):
        return {
            "contextName": "default",
            "schemaVersion": "1.8.1",
            "metadataset": "event.json",
            "language": "de",
            "processing": {
                "status": "failed",
                "success": False,
                "error": {"code": "LLM_UPSTREAM_ERROR", "message": "Fehler 500."},
            },
        }


def test_a_text_extraction_failure_names_the_id_in_body_and_log(monkeypatch, capsys):
    monkeypatch.setattr(deps, "get_input_source_service", lambda: _FailingInput())

    with TestClient(main.app) as client:
        response = client.post(
            "/generate",
            json={"input_source": "url", "source_url": "https://example.org"},
            headers={"X-Request-ID": "trace-42"},
        )

    assert response.status_code == 504
    assert response.json()["error"]["request_id"] == "trace-42"
    log = capsys.readouterr().out
    assert "trace-42" in log and "TEXT_EXTRACTION_CONNECT_TIMEOUT" in log


def test_an_llm_failure_names_the_id(monkeypatch, capsys):
    monkeypatch.setattr(deps, "get_llm_service", lambda **kw: _RefusedLLM())

    with TestClient(main.app) as client:
        response = client.post(
            "/extract-field",
            json={
                "text": "Ein Text",
                "schema_file": "core.json",
                "field_id": "cclom:title",
            },
        )

    assert response.status_code == 502
    request_id = response.headers["x-request-id"]
    assert response.json()["error"]["request_id"] == request_id
    assert request_id in capsys.readouterr().out


def test_a_failed_generation_names_the_id(monkeypatch):
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: _FailedGeneration())

    with TestClient(main.app) as client:
        response = client.post("/generate", json={"text": "Ein Text"})

    assert response.status_code == 502
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


# ------------------------------------------------- tracing across services
#
# Team 6 runs services behind Envoy/Istio: tracing is B3, and
# x-client-trace-id is passed on so logs can be filtered across services.
# The agent forwards those headers unchanged to its own services (B-API, text
# extraction, repository) - never to third parties such as the geocoder.


def test_the_client_trace_id_becomes_the_request_id():
    with TestClient(main.app) as client:
        response = client.get(
            "/health",
            headers={"x-client-trace-id": "trace-abc", "X-Request-ID": "other"},
        )

    assert response.headers["x-request-id"] == "trace-abc"


def _recording_client(seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), event_hooks=TRACE_EVENT_HOOKS
    )


@pytest.mark.asyncio
async def test_trace_headers_reach_the_agents_own_services():
    seen = []
    incoming = {
        "x-client-trace-id": "trace-abc",
        "x-b3-traceid": "80f198ee56343ba864fe8b2a57d3eff7",
        "x-b3-spanid": "e457b5a2e4d86bd1",
        "x-b3-sampled": "1",
        "b3": "80f198ee56343ba864fe8b2a57d3eff7-e457b5a2e4d86bd1-1",
        "cookie": "session=secret",
        "authorization": "Bearer user-key",
    }

    with traced(incoming):
        await _recording_client(seen).get("http://repository.example/rest")

    sent = seen[0].headers
    for name in (
        "x-client-trace-id",
        "x-b3-traceid",
        "x-b3-spanid",
        "x-b3-sampled",
        "b3",
    ):
        assert sent[name] == incoming[name]
    # The id the agent logs under goes along, so its log lines can be matched.
    assert sent["x-request-id"] == "trace-abc"
    # Only tracing headers - never cookies or credentials.
    assert "cookie" not in sent and "authorization" not in sent


@pytest.mark.asyncio
async def test_values_that_are_not_plain_tokens_are_replaced():
    seen = []

    with traced({"x-b3-traceid": "abc def", "x-b3-spanid": "<x>"}):
        await _recording_client(seen).get("http://repository.example/rest")

    assert GENERATED.fullmatch(seen[0].headers["x-b3-traceid"])
    assert SPAN.fullmatch(seen[0].headers["x-b3-spanid"])


@pytest.mark.asyncio
async def test_without_b3_headers_a_new_trace_starts_and_is_passed_on():
    seen = []

    with traced({}) as request_id:
        ids = current_trace_ids()
        await _recording_client(seen).get("http://repository.example/rest")

    sent = seen[0].headers
    assert GENERATED.fullmatch(sent["x-b3-traceid"])
    assert SPAN.fullmatch(sent["x-b3-spanid"])
    # One id to quote and to search for: request id and trace id are the same.
    assert request_id == sent["x-b3-traceid"] == sent["x-request-id"]
    assert ids == {
        "trace_id": sent["x-b3-traceid"],
        "span_id": sent["x-b3-spanid"],
        "request_id": request_id,
    }


def test_an_incoming_trace_id_becomes_the_request_id():
    incoming = {
        "x-b3-traceid": "80f198ee56343ba864fe8b2a57d3eff7",
        "x-b3-spanid": "e457b5a2e4d86bd1",
    }

    with traced(incoming) as request_id:
        ids = current_trace_ids()

    assert request_id == "80f198ee56343ba864fe8b2a57d3eff7"
    assert ids["span_id"] == "e457b5a2e4d86bd1"


@pytest.mark.asyncio
async def test_the_single_b3_header_is_read_and_passed_on_alone():
    seen = []
    b3 = "80f198ee56343ba864fe8b2a57d3eff7-e457b5a2e4d86bd1-1"

    with traced({"b3": b3}):
        ids = current_trace_ids()
        await _recording_client(seen).get("http://repository.example/rest")

    assert ids["trace_id"] == "80f198ee56343ba864fe8b2a57d3eff7"
    assert ids["span_id"] == "e457b5a2e4d86bd1"
    assert seen[0].headers["b3"] == b3
    # No second, different trace next to the one that came in.
    assert "x-b3-traceid" not in seen[0].headers


def test_the_client_trace_id_is_kept_next_to_the_trace_id():
    with traced({"x-client-trace-id": "trace-abc"}) as request_id:
        ids = current_trace_ids()

    assert request_id == ids["request_id"] == ids["client_trace_id"] == "trace-abc"
    assert GENERATED.fullmatch(ids["trace_id"])


def test_outside_a_request_there_are_no_ids():
    assert current_trace_ids() == {}


@pytest.mark.asyncio
async def test_outside_a_request_nothing_is_added():
    seen = []

    await _recording_client(seen).get("http://repository.example/rest")

    assert "x-request-id" not in seen[0].headers


def test_the_agents_own_clients_carry_the_trace_hook(monkeypatch):
    from src.services.input_source_service import InputSourceService
    from src.services.llm_service import LLMService

    for client in (LLMService().http_client, InputSourceService().http_client):
        assert propagate_trace in client.event_hooks["request"]


# ------------------------------------------------- B3 ids are what B3 says
#
# Audit 13.11., L-22: any token of up to 128 characters passed as trace id, and
# became the request id and X-Request-ID. A trace id is 16 or 32 hex
# characters, a span id 16; anything else is replaced by a new trace.


@pytest.mark.parametrize(
    "incoming",
    [
        {"x-b3-traceid": "x" * 128, "x-b3-spanid": "e457b5a2e4d86bd1"},
        {"x-b3-traceid": "not-hex-at-all", "x-b3-spanid": "e457b5a2e4d86bd1"},
        {"x-b3-spanid": "e457b5a2e4d86bd1"},
        {"b3": "abc-def"},
    ],
)
def test_ids_that_are_not_b3_start_a_new_trace(incoming):
    with traced(incoming) as request_id:
        ids = current_trace_ids()

    assert GENERATED.fullmatch(ids["trace_id"])
    assert SPAN.fullmatch(ids["span_id"])
    assert request_id == ids["trace_id"]
    assert ids["trace_id"] != incoming.get("x-b3-traceid")


def test_a_trace_with_an_unusable_span_gets_a_new_span():
    incoming = {"x-b3-traceid": "80f198ee56343ba8", "x-b3-spanid": "zzzzzzzzzzzzzzzz"}

    with traced(incoming):
        ids = current_trace_ids()

    assert ids["trace_id"] == "80f198ee56343ba8"
    assert SPAN.fullmatch(ids["span_id"])


def test_a_short_trace_id_is_kept():
    incoming = {"x-b3-traceid": "80f198ee56343ba8", "x-b3-spanid": "e457b5a2e4d86bd1"}

    with traced(incoming):
        ids = current_trace_ids()

    assert (ids["trace_id"], ids["span_id"]) == ("80f198ee56343ba8", "e457b5a2e4d86bd1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state, header, value",
    [("0", "x-b3-sampled", "0"), ("1", "x-b3-sampled", "1"), ("d", "x-b3-flags", "1")],
)
async def test_a_bare_sampling_state_is_passed_on_with_the_new_trace(
    state, header, value
):
    """`b3: 0` names no trace; next to new x-b3-* ids a receiver would prefer it."""
    seen = []

    with traced({"b3": state}):
        await _recording_client(seen).get("http://repository.example/rest")

    sent = seen[0].headers
    assert "b3" not in sent
    assert sent[header] == value
    assert GENERATED.fullmatch(sent["x-b3-traceid"])


def test_a_response_never_carries_an_overlong_id():
    with TestClient(main.app) as client:
        response = client.get("/health", headers={"x-b3-traceid": "a" * 128})

    assert GENERATED.fullmatch(response.headers["x-request-id"])
