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

    await _public_requests_only([])(route)

    assert route.aborted == "blockedbyclient"
    _assert_logged_without_secrets(caplog, "http")


@pytest.mark.asyncio
async def test_a_blocked_websocket_is_logged_without_query_or_credentials(caplog):
    socket = _Socket("ws" + SECRET_URL[len("http") :])

    await _public_websockets_only()(socket)

    assert socket.closed is not None
    _assert_logged_without_secrets(caplog, "ws")


async def test_a_page_that_cannot_be_loaded_is_logged_without_query(caplog):
    from playwright.async_api import Error as PlaywrightError

    from src.services.screenshot_service import ScreenshotError, _open

    class _Page:
        async def goto(self, url, **kwargs):
            raise PlaywrightError(
                f"Page.goto: net::ERR_CONNECTION_REFUSED at {SECRET_URL}\n"
                f'Call log:\n  - navigating to "{SECRET_URL}"'
            )

    caplog.set_level("INFO")
    with pytest.raises(ScreenshotError):
        await _open(_Page(), SECRET_URL.replace("user:passwort@", ""), [])

    for secret in ("token", "geheim", "passwort", "abschnitt"):
        assert secret not in caplog.text, secret


async def test_text_extraction_logs_the_url_without_query(caplog, monkeypatch):
    import socket

    import httpx

    from src.services.input_source_service import InputSourceService

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )

    async def post(self, url, json=None, **kwargs):
        return httpx.Response(
            200, json={"text": ""}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    caplog.set_level("INFO")

    with pytest.raises(Exception) as error:
        await InputSourceService().fetch_from_url("https://example.org/a?token=geheim")

    assert "https://example.org/a" in caplog.text
    assert "geheim" not in caplog.text
    assert "geheim" not in str(error.value)
