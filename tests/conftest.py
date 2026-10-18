"""
Run every test against the same, known configuration.

Settings are read from the process environment and from `.env`, and `src.main`
builds its settings at import time. Without this, a developer's exported
METADATA_AGENT_* values, a local `.env`, or real API keys decide whether a test
passes — and real keys would reach code under test. This runs before any test
module imports `src`, so even import-time settings see the clean environment.
"""

import os

# Credentials the settings read without the METADATA_AGENT_ prefix (aliases)
# plus the variants a developer environment typically holds.
_UNPREFIXED = (
    "B_API_KEY",
    "B_API_KEY_PROD",
    "B_API_KEY_STAGING",
    "OPENAI_API_KEY",
    "WLO_GUEST_USERNAME",
    "WLO_GUEST_PASSWORD",
)

for _name in list(os.environ):
    if _name.startswith("METADATA_AGENT_") or _name in _UNPREFIXED:
        del os.environ[_name]

# A placeholder, never a real key: code paths that need *a* static key get one,
# and nothing in the test run can authenticate anywhere.
os.environ["B_API_KEY"] = "test-b-api-key"

from src.config import Settings  # noqa: E402

# A developer's .env must not leak in either.
Settings.model_config["env_file"] = None


# ------------------------------------------------------------ network guard
# Tests run offline (audit 18.10. final): a test that reaches an external
# service depends on it, and could carry data out. Loopback stays open for
# TestClient, the local test servers and the browser tests; DNS lookups are not
# connections and stay possible. RUN_NETWORK_TESTS=1 lifts the guard for all
# tests. Both entry points are covered: socket.connect (sync clients, selector
# loops) and the loops' sock_connect (Windows' proactor connects without
# socket.connect).
import asyncio.proactor_events  # noqa: E402
import asyncio.selector_events  # noqa: E402
import ipaddress  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402

import pytest  # noqa: E402

# (owning test, address) of every refused connection. A thread belongs to the
# test that started it: a refusal of a thread that outlived its test is not
# blamed on the test running at the time (review 18.10.).
_external_connects: list[tuple[str, str]] = []


def _current_test() -> str:
    return os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" (", 1)[0]


def _owner() -> str:
    return getattr(threading.current_thread(), "_test_owner", "") or _current_test()


def _is_local(address) -> bool:
    if not isinstance(address, tuple):
        return True  # AF_UNIX and the like
    host = str(address[0]).split("%", 1)[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _refuse_external(address) -> None:
    if not _is_local(address):
        _external_connects.append((_owner(), str(address)))
        raise RuntimeError(
            f"Test tried to connect to {address}: tests run offline "
            "(RUN_NETWORK_TESTS=1 lifts the guard)."
        )


def _guarded(connect):
    def guarded(self, *args):
        _refuse_external(args[-1])
        return connect(self, *args)

    return guarded


def _owned(start):
    def owned(self):
        self._test_owner = _current_test()
        return start(self)

    return owned


if os.environ.get("RUN_NETWORK_TESTS") != "1":
    socket.socket.connect = _guarded(socket.socket.connect)  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded(socket.socket.connect_ex)  # type: ignore[method-assign]
    for _loop in (
        asyncio.proactor_events.BaseProactorEventLoop,
        asyncio.selector_events.BaseSelectorEventLoop,
    ):
        _loop.sock_connect = _guarded(_loop.sock_connect)  # type: ignore[method-assign]
    threading.Thread.start = _owned(threading.Thread.start)  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _no_external_connections(request):
    """Fail the test even when the code under test swallowed the refusal."""
    yield
    mine = [
        address for owner, address in _external_connects if owner == request.node.nodeid
    ]
    _external_connects[:] = [
        e for e in _external_connects if e[0] != request.node.nodeid
    ]
    assert not mine, f"external connections: {mine}"


def pytest_sessionfinish(session):
    """Refusals no test reported (its thread outlived it) fail the run."""
    if _external_connects:
        print(f"\nexternal connections after their test: {_external_connects}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
