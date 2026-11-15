"""
A client that closes the connection, seen at the three AI routes (audit 14.11.,
S-36 and Q-01).

The answer is 499, the work is cancelled, and it is no failed generation: the
run counts as `cancelled`, so the alert on failed generations does not fire
when a proxy gives up early. A source that cannot be fetched does count as
`failed`. The requests go straight into the ASGI app: only there a client can
leave while the handler runs.
"""

import asyncio
import json

import pytest

from src import main
from src.api import deps
from src.api.routers import generate as generate_module
from src.metrics import GENERATIONS, HTTP_REQUESTS
from src.services.input_source_service import TextExtractionError

RESULT = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "event.json",
    "language": "de",
    "exportedAt": "2026-11-14T00:00:00Z",
    "metadata": {"cclom:title": "Kurs"},
    "processing": {"status": "success", "success": True, "warnings": []},
}


class _Held:
    """Stands in for the metadata, LLM and input service: every call waits."""

    llm_service = None
    provider = model = "fake"

    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = False

    async def _wait(self, *args, **kwargs):
        self.started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    generate_metadata = detect_schema = extract_field = fetch_from_url = _wait

    async def close(self):
        pass


async def _call(path: str, body: dict, leaves_after: asyncio.Event | None) -> int:
    """One request; the client leaves once `leaves_after` is set. The status."""
    raw = json.dumps(body).encode()
    sent: list[dict] = []
    body_given = False

    async def receive():
        nonlocal body_given
        if not body_given:
            body_given = True
            return {"type": "http.request", "body": raw, "more_body": False}
        if leaves_after is None:
            await asyncio.Event().wait()
        else:
            await leaves_after.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"host", b"test")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    await asyncio.wait_for(main.app(scope, receive, send), 10)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _generations(status: str) -> float:
    return GENERATIONS.labels(status)._value.get()


def _requests(path: str, status: int) -> float:
    return HTTP_REQUESTS.labels("POST", path, str(status))._value.get()


@pytest.fixture
def held(monkeypatch):
    service = _Held()
    monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: service)
    monkeypatch.setattr(deps, "get_llm_service", lambda **kw: service)
    monkeypatch.setattr(deps, "get_input_source_service", lambda: service)
    return service


@pytest.mark.parametrize(
    "path, body",
    [
        ("/generate", {"text": "Ein Text"}),
        ("/generate", {"input_source": "url", "source_url": "https://example.org/"}),
        ("/detect-content-type", {"text": "Ein Text"}),
        (
            "/extract-field",
            {
                "text": "Ein Text",
                "schema_file": "event.json",
                "field_id": "cclom:title",
            },
        ),
    ],
    ids=["generate", "generate-while-fetching", "detect-content-type", "extract-field"],
)
async def test_a_client_that_leaves_gets_499_and_is_no_failed_generation(
    held, path, body
):
    failed, cancelled = _generations("failed"), _generations("cancelled")
    answered = _requests(path, 499)

    status = await _call(path, body, leaves_after=held.started)

    assert status == 499
    assert held.cancelled
    assert _generations("failed") == failed
    assert _requests(path, 499) == answered + 1
    counted = 1 if path == "/generate" else 0  # only /generate counts runs
    assert _generations("cancelled") == cancelled + counted
    assert generate_module._running_generations == 0


class _Source:
    def __init__(self, error: Exception):
        self.error = error

    async def fetch_from_url(self, *args, **kwargs):
        raise self.error


@pytest.mark.parametrize(
    "error, status",
    [
        (TextExtractionError("TEXT_EXTRACTION_TIMEOUT", "Zeitlimit", 504), 504),
        (RuntimeError("down"), 502),
    ],
)
async def test_a_source_that_cannot_be_fetched_is_a_failed_generation(
    monkeypatch, error, status
):
    monkeypatch.setattr(deps, "get_input_source_service", lambda: _Source(error))
    failed = _generations("failed")

    answer = await _call(
        "/generate",
        {"input_source": "url", "source_url": "https://example.org/"},
        leaves_after=None,
    )

    assert answer == status
    assert _generations("failed") == failed + 1


async def test_a_request_the_caller_got_wrong_is_no_failed_generation(monkeypatch):
    failed = _generations("failed")

    answer = await _call("/generate", {"input_source": "url"}, leaves_after=None)

    assert answer == 400
    assert _generations("failed") == failed


async def test_a_page_without_text_is_no_failed_generation(monkeypatch):
    empty = TextExtractionError("TEXT_EXTRACTION_EMPTY", "Kein Text", 422)
    monkeypatch.setattr(deps, "get_input_source_service", lambda: _Source(empty))
    failed = _generations("failed")

    answer = await _call(
        "/generate",
        {"input_source": "url", "source_url": "https://example.org/"},
        leaves_after=None,
    )

    assert answer == 422
    assert _generations("failed") == failed


# ------------------------------------------------------------ before the body


async def _call_and_leave_mid_body(path: str) -> int:
    """The client sends half a body and closes the connection."""
    sent: list[dict] = []
    messages = [
        {"type": "http.request", "body": b'{"text": "Ein', "more_body": True},
        {"type": "http.disconnect"},
    ]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"host", b"test")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    await asyncio.wait_for(main.app(scope, receive, send), 10)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.parametrize(
    "path", ["/generate", "/extract-field", "/validate", "/upload", "/screenshot"]
)
async def test_a_client_that_leaves_before_the_end_of_the_body_is_a_499(path, caplog):
    answered = _requests(path, 499)
    failed, cancelled = _generations("failed"), _generations("cancelled")

    with caplog.at_level("ERROR"):
        status = await _call_and_leave_mid_body(path)

    assert status == 499
    assert _requests(path, 499) == answered + 1
    # No run began, and nothing of the service went wrong.
    assert (_generations("failed"), _generations("cancelled")) == (failed, cancelled)
    assert not caplog.records
    assert generate_module._running_generations == 0
