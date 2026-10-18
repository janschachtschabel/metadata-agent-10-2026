"""
A blocked browser request is logged without query string or credentials.

The URL policy of the screenshot browser logged every refused request in full
(review 18.10.): query strings carry session ids and tokens, the user info of a
URL a password. The log needs scheme, host and path to explain the refusal.
"""

from types import SimpleNamespace

import pytest

from src.services.screenshot_service import (
    _public_requests_only,
    _public_websockets_only,
)

SECRET_URL = "http://user:passwort@10.0.0.1:8080/intern/seite?token=geheim#abschnitt"


class _Route:
    def __init__(self, url):
        self.request = SimpleNamespace(url=url)
        self.aborted = None

    async def continue_(self):
        raise AssertionError("an internal request must not continue")

    async def abort(self, reason):
        self.aborted = reason


class _Socket:
    def __init__(self, url):
        self.url = url
        self.closed = None

    def connect_to_server(self):
        raise AssertionError("an internal socket must not connect")

    async def close(self, code=None, reason=None):
        self.closed = (code, reason)


def _assert_logged_without_secrets(caplog, scheme):
    assert f"{scheme}://10.0.0.1:8080/intern/seite" in caplog.text
    for secret in ("token", "geheim", "passwort", "user", "abschnitt"):
        assert secret not in caplog.text, secret


@pytest.mark.asyncio
async def test_a_blocked_request_is_logged_without_query_or_credentials(caplog):
    route = _Route(SECRET_URL)

    await _public_requests_only()(route)

    assert route.aborted == "blockedbyclient"
    _assert_logged_without_secrets(caplog, "http")


@pytest.mark.asyncio
async def test_a_blocked_websocket_is_logged_without_query_or_credentials(caplog):
    socket = _Socket("ws" + SECRET_URL[len("http") :])

    await _public_websockets_only()(socket)

    assert socket.closed is not None
    _assert_logged_without_secrets(caplog, "ws")
