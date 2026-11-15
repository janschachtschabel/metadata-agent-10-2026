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
def forget_refusals(request):
    """
    These tests provoke refusals on purpose; the autouse check must not count
    them. Only their own: another test's refusal still fails the run.
    """
    yield
    conftest._external_connects[:] = [
        e for e in conftest._external_connects if e[0] != request.node.nodeid
    ]


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
# test that started it, not to the one running when it connects. One test, so
# the result does not depend on the order of two (audit 14.11., Q-04).
def test_a_late_refusal_belongs_to_the_test_that_started_the_thread(
    forget_refusals, request, monkeypatch
):
    go = threading.Event()

    def connect_later():
        go.wait(10)
        with socket.socket() as sock, contextlib.suppress(RuntimeError):
            sock.connect(PUBLIC)

    thread = threading.Thread(target=connect_later, daemon=True)
    thread.start()
    # The thread connects while pytest reports the next test as running.
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_next.py::test_next (call)")
    go.set()
    thread.join(10)

    assert conftest._external_connects == [(request.node.nodeid, str(PUBLIC))]
