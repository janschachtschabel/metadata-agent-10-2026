"""
The test run stays offline, loopback stays open (conftest.py, audit 18.10. final).

Runs only without RUN_NETWORK_TESTS=1 - with it, the guard is lifted.
"""

import contextlib
import os
import socket
import threading

import conftest
import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_NETWORK_TESTS") == "1", reason="guard lifted"
)

PUBLIC = ("93.184.216.34", 80)


@pytest.fixture
def forget_refusals():
    """These tests provoke refusals on purpose; the autouse check must not count them."""
    yield
    conftest._external_connects.clear()


def test_a_blocking_connection_to_the_internet_is_refused(forget_refusals):
    with socket.socket() as sock, pytest.raises(RuntimeError, match="offline"):
        sock.connect(PUBLIC)


async def test_an_async_connection_to_the_internet_is_refused(forget_refusals, request):
    # anyio may wrap the refusal in an exception group.
    async with httpx.AsyncClient() as client:
        with pytest.raises(BaseException):
            await client.get(f"http://{PUBLIC[0]}/")

    assert conftest._external_connects == [(request.node.nodeid, str(PUBLIC))]


def test_a_swallowed_refusal_is_still_recorded(forget_refusals):
    try:
        httpx.get(f"http://{PUBLIC[0]}/")
    except Exception:
        pass

    assert conftest._external_connects


def test_loopback_stays_open():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    with server, socket.create_connection(server.getsockname(), timeout=2):
        pass


# A thread that outlives its test (review 18.10.): its refusal belongs to the
# test that started it, not to the one running when it connects.
_leftover: dict = {}


def test_a_thread_outlives_its_test():
    go = threading.Event()

    def connect_later():
        go.wait(10)
        with socket.socket() as sock, contextlib.suppress(RuntimeError):
            sock.connect(PUBLIC)

    thread = threading.Thread(target=connect_later, daemon=True)
    thread.start()
    _leftover.update(thread=thread, go=go)


def test_its_late_refusal_is_not_blamed_on_the_next_test(request):
    if "thread" not in _leftover:
        pytest.skip("runs after test_a_thread_outlives_its_test")
    _leftover["go"].set()
    _leftover["thread"].join(10)

    starter = (
        request.node.nodeid.rsplit("::", 1)[0] + "::test_a_thread_outlives_its_test"
    )
    try:
        assert conftest._external_connects == [(starter, str(PUBLIC))]
    finally:
        conftest._external_connects.clear()
