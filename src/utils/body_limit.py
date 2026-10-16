"""
Upper bound for request bodies.

Endpoints read the whole body into memory, and an invalid JSON body is then
repaired character by character on the event loop. Without a bound, one large
request stalls the worker for everyone. The declared Content-Length is checked
before anything is read; a streamed (chunked) body is read up to the limit and
then handed on.
"""

import json
from typing import Callable, Union


async def _send_too_large(send, limit: int) -> None:
    body = json.dumps(
        {
            "detail": (
                f"Anfrage zu groß: höchstens {limit} Bytes "
                "(METADATA_AGENT_MAX_REQUEST_BYTES)."
            )
        },
        ensure_ascii=False,
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """
    Pure ASGI middleware answering 413 for bodies above `max_bytes`.

    `max_bytes` may be a callable so the limit follows the settings object the
    app currently uses.
    """

    def __init__(self, app, max_bytes: Union[int, Callable[[], int]]):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        limit = self.max_bytes() if callable(self.max_bytes) else self.max_bytes

        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    break
                if declared > limit:
                    await _send_too_large(send, limit)
                    return
                break

        # Read the body up to the limit before the app sees it, then replay it.
        # Raising from inside receive() instead would land in the endpoints'
        # own `except Exception` and come back as a 400 parse error.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # Client went away before the body was complete.
                await self.app(scope, _replay([message], receive), send)
                return
            body += message.get("body", b"")
            if len(body) > limit:
                await _send_too_large(send, limit)
                return
            if not message.get("more_body"):
                break

        complete = {"type": "http.request", "body": bytes(body), "more_body": False}
        # Only the copy handed on may stay alive while the app runs; holding the
        # buffer and the last chunk too would cost a large body 2-3x its size.
        del body, message
        await self.app(scope, _replay([complete], receive), send)


def _replay(messages: list, receive):
    """A receive() that hands out the buffered messages first, then delegates."""
    pending = list(messages)

    async def replay():
        if pending:
            return pending.pop(0)
        return await receive()

    return replay
