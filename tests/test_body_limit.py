"""
Request bodies have an upper bound.

Without one, a large body is read into memory in full, and an invalid one is
then walked character by character by sanitize_json_string on the event loop —
the whole worker stalls. Both the declared length and a streamed (chunked)
body count.
"""

import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.utils.body_limit import BodySizeLimitMiddleware


async def _echo_app(scope, receive, send):
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": str(len(body)).encode()})


async def _call(app, chunks, headers=()):
    sent = []
    pending = list(chunks)

    async def receive():
        body = pending.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(pending)}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/", "headers": list(headers)}
    await app(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_a_body_within_the_limit_passes():
    sent = await _call(BodySizeLimitMiddleware(_echo_app, max_bytes=10), [b"12345"])

    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b"5"


@pytest.mark.asyncio
async def test_a_declared_length_over_the_limit_is_refused_unread():
    sent = await _call(
        BodySizeLimitMiddleware(_echo_app, max_bytes=10),
        [b"x" * 11],
        headers=[(b"content-length", b"11")],
    )

    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_a_streamed_body_over_the_limit_is_refused():
    sent = await _call(
        BodySizeLimitMiddleware(_echo_app, max_bytes=10), [b"x" * 6, b"x" * 6]
    )

    assert sent[0]["status"] == 413


def test_the_api_refuses_an_oversized_generate_request(monkeypatch):
    monkeypatch.setattr(deps.settings, "max_request_bytes", 1024)

    response = TestClient(main.app).post("/generate", content=b"{" + b" " * 2048 + b"}")

    assert response.status_code == 413


def test_a_chunked_oversized_request_is_a_413_not_a_parse_error(monkeypatch):
    # Without Content-Length the limit trips while the endpoint reads the
    # body; /generate used to turn that into "400 Request parsing error".
    monkeypatch.setattr(deps.settings, "max_request_bytes", 1024)

    def chunks():
        yield b"{"
        yield b" " * 2048
        yield b"}"

    response = TestClient(main.app).post("/generate", content=chunks())

    assert response.status_code == 413


def test_a_413_carries_the_cors_headers_the_widget_needs(monkeypatch):
    # The widget runs on another origin; without the header the browser hides
    # the answer and reports a bare network error.
    monkeypatch.setattr(deps.settings, "max_request_bytes", 1024)

    response = TestClient(main.app).post(
        "/generate",
        content=b"{" + b" " * 2048 + b"}",
        headers={"Origin": "https://redaktion.example"},
    )

    assert response.status_code == 413
    assert "access-control-allow-origin" in response.headers


@pytest.mark.asyncio
async def test_the_buffered_body_is_not_held_twice_while_the_app_runs():
    # The middleware's own buffer must be released once it handed the body on;
    # otherwise a body near the limit costs twice its size for the whole request.
    import tracemalloc

    chunk = 4 * 1024 * 1024
    sizes = []

    async def measuring_app(scope, receive, send):
        message = await receive()
        sizes.append((len(message["body"]), tracemalloc.get_traced_memory()[0]))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    calls = iter([True, False])

    async def receive():
        # Built on demand, so the test itself holds no copy.
        return {"type": "http.request", "body": b"x" * chunk, "more_body": next(calls)}

    async def send(message):
        pass

    tracemalloc.start()
    try:
        middleware = BodySizeLimitMiddleware(measuring_app, max_bytes=20 * 1024 * 1024)
        await middleware({"type": "http", "headers": []}, receive, send)
    finally:
        tracemalloc.stop()

    ((length, in_use),) = sizes
    assert length == 2 * chunk
    assert in_use < 1.5 * length
