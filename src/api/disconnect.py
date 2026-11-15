"""
Stop work nobody waits for: the client closed the connection.

uvicorn does not cancel a handler when the connection goes away. A proxy that
gave up after its own timeout left a /generate running for up to 300 s - some
50 LLM calls for nobody (audit 13.11., P-10). Only for the AI endpoints that
write nothing: a write to the repository is finished, whoever is listening.
"""

import asyncio
from typing import Any, Callable, Coroutine, TypeVar

from fastapi import Request, Response

T = TypeVar("T")


class ClientDisconnected(Exception):
    """The client is gone; the work was stopped."""


async def _gone(request: Request) -> None:
    """
    Return when the connection is closed.

    The body has been read, so the next message is the disconnect. Waited for
    directly: Request.is_disconnected() polls inside a cancel scope that
    swallows a cancellation - the watcher could then not be stopped.
    """
    while (await request.receive()).get("type") != "http.disconnect":
        pass


async def run_unless_disconnected(request: Request, work: Coroutine[Any, Any, T]) -> T:
    """
    The result of `work` - or ClientDisconnected, with the work cancelled.

    A cancellation from outside (a deadline around the call) cancels the work
    as well.
    """
    task = asyncio.ensure_future(work)
    watcher = asyncio.ensure_future(_gone(request))
    try:
        await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            return task.result()
        raise ClientDisconnected()
    finally:
        for pending in (task, watcher):
            pending.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)


# nginx's status for "client closed request": the answer reaches nobody, it is
# what the request line in the log and the metrics show.
CLIENT_CLOSED_REQUEST = 499


async def run_with_deadline(
    request: Request,
    work: Coroutine[Any, Any, T],
    seconds: float,
    timeout_response: Callable[[float], Response],
) -> T | Response:
    """
    The result of `work` within `seconds` - else `timeout_response(seconds)`,
    or 499 when the client left. The work is cancelled in both cases.

    For the AI endpoints that answer with one call's result
    (/detect-content-type, /extract-field).
    """
    try:
        async with asyncio.timeout(seconds):
            return await run_unless_disconnected(request, work)
    except TimeoutError:
        return timeout_response(seconds)
    except ClientDisconnected:
        # Nobody waits for the answer any more: the AI call was stopped.
        return Response(status_code=CLIENT_CLOSED_REQUEST)
