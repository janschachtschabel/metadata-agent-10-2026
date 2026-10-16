"""
Public URL policy for every feature that makes this service open a URL.

Screenshots run a browser inside this container, preview images are downloaded
by it, and the text extraction crawler fetches what it is handed. None of them
may be steered at loopback, link-local or private addresses.
"""

import gzip
import socket
from unittest.mock import AsyncMock

import httpx
import pytest

from src.services import screenshot_service as screenshot_module
from src.services.input_source_service import InputSourceService
from src.services.repository_service import (
    MAX_PREVIEW_BYTES,
    PreviewError,
    resolve_preview,
)
from src.utils.public_url import PublicUrlError, require_public_http_url

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _address(ip: str):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (ip, 443))]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://[::1]/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.8/private",
        "http://localhost/internal",
        "http://printer.local/",
        "file:///etc/passwd",
        "https://user:password@example.org/",
        "http://224.0.0.1/",
        "http://[64:ff9b::a00:1]/",
        "http://[2002:0a00:0001::1]/",
        # Deprecated IPv6 site-local space (RFC 3879) — still routed internally.
        "http://[fec0::1]/",
        "http://0.0.0.0/",
        "http://[::ffff:127.0.0.1]/",
        "http://127.1/",
        # Fullwidth letters: browsers map them to 'printer.local'.
        "http://printer.ｌｏｃａｌ/",
        "",
    ],
)
async def test_non_public_targets_are_rejected(url):
    with pytest.raises(PublicUrlError):
        await require_public_http_url(url)


async def test_an_international_hostname_is_checked_as_httpx_connects_to_it(
    monkeypatch,
):
    """
    Python's 'idna' codec implements IDNA 2003 and turns 'ß' into 'ss'; httpx
    (IDNA 2008) connects to 'xn--zca'. Checking 'ss.example' would let anyone
    owning the 'ß' name point the preview download at an internal address.
    """
    answers = {"xn--zca.example": "10.0.0.4", "ss.example": "93.184.216.34"}
    looked_up: list[str] = []

    def resolve(host, *args, **kwargs):
        looked_up.append(host)
        return _address(answers[host])

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    with pytest.raises(PublicUrlError):
        await require_public_http_url("https://ß.example/preview.png")

    assert looked_up == ["xn--zca.example"]


async def test_the_host_check_does_not_depend_on_the_url_length():
    """httpx refuses to parse a URL over 64 KiB; only the host matters here."""
    url = "http://93.184.216.34/beacon?" + "x" * 70_000

    assert await require_public_http_url(url) == url


async def test_a_public_international_hostname_is_accepted(monkeypatch):
    looked_up: list[str] = []

    def resolve(host, *args, **kwargs):
        looked_up.append(host)
        return _address("93.184.216.34")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    url = "https://Bücher.example/titel.png"
    assert await require_public_http_url(url) == url
    assert looked_up == ["xn--bcher-kva.example"]


async def test_a_hostname_resolving_to_a_private_address_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _address("10.0.0.4"))

    with pytest.raises(PublicUrlError, match="öffentliche"):
        await require_public_http_url("https://example.org/private")


async def test_mixed_public_and_private_dns_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: _address("93.184.216.34") + _address("192.168.1.2"),
    )

    with pytest.raises(PublicUrlError):
        await require_public_http_url("https://example.org")


async def test_an_unresolvable_hostname_is_rejected_with_a_clear_reason(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", fail)

    with pytest.raises(PublicUrlError, match="aufgelöst"):
        await require_public_http_url("https://does-not-exist.example")


async def test_a_public_hostname_is_accepted(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: _address("93.184.216.34")
    )

    assert (
        await require_public_http_url("https://example.org/resource")
        == "https://example.org/resource"
    )


# ------------------------------------------------------------- screenshots


async def test_screenshot_checks_the_target_before_starting_the_browser(monkeypatch):
    service = screenshot_module.ScreenshotService()
    browser = AsyncMock(side_effect=AssertionError("browser must not start"))
    monkeypatch.setattr(service, "_capture_playwright", browser)

    with pytest.raises(PublicUrlError):
        await service.capture("http://127.0.0.1/private", method="pageshot")

    browser.assert_not_awaited()


# ---------------------------------------------------------- preview download


async def test_preview_redirect_to_an_internal_address_is_blocked():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PreviewError, match="nicht öffentlich"):
            await resolve_preview(client, "http://93.184.216.34/start")

    assert requests == ["http://93.184.216.34/start"]


async def test_preview_follows_redirects_between_public_hosts():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://93.184.216.35/img"})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        image, mimetype = await resolve_preview(client, "http://93.184.216.34/start")

    assert (image, mimetype) == (PNG, "image/png")


async def test_preview_declared_over_the_size_limit_is_rejected_before_reading():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "image/png",
                "content-length": str(MAX_PREVIEW_BYTES + 1),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PreviewError, match="KB"):
            await resolve_preview(client, "http://93.184.216.34/image.png")


async def test_a_streamed_preview_without_a_length_is_cut_off_at_the_limit():
    chunk = b"\x00" * 65536
    sent = 0

    async def endless_body():
        nonlocal sent
        while sent < 4 * MAX_PREVIEW_BYTES:
            sent += len(chunk)
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        # An async body is sent chunked, without content-length.
        return httpx.Response(
            200, headers={"content-type": "image/png"}, content=endless_body()
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PreviewError, match="KB"):
            await resolve_preview(client, "http://93.184.216.34/image.png")

    assert sent <= MAX_PREVIEW_BYTES + len(chunk)


async def test_a_compressed_preview_is_refused_instead_of_inflated():
    """
    Decoding first would inflate a single chunk of stacked gzip far beyond the
    limit before it is counted. The request asks for identity encoding.
    """
    accept_encoding: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        accept_encoding.append(request.headers.get("accept-encoding", ""))
        return httpx.Response(
            200,
            headers={"content-type": "image/png", "content-encoding": "gzip, gzip"},
            content=gzip.compress(gzip.compress(PNG)),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PreviewError, match="komprimiert"):
            await resolve_preview(client, "http://93.184.216.34/image.png")

    assert accept_encoding == ["identity"]


async def test_a_preview_url_httpx_cannot_send_is_a_preview_error():
    """httpx.InvalidURL is no HTTPError subclass; it must not escape either."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=PNG))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(PreviewError):
            await resolve_preview(client, "http://93.184.216.34/pa\x00th.png")


@pytest.mark.parametrize("location", ["http://[", "http://[::1]@", "http://[@]"])
async def test_a_malformed_preview_redirect_is_reported_as_a_preview_error(location):
    """
    httpx accepts these Location values, urllib's urljoin raises ValueError.
    Anything but PreviewError escapes _set_preview, and save_node then discards
    the node it has just written.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": location})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PreviewError):
            await resolve_preview(client, "http://93.184.216.34/start")


# ------------------------------------------------------ text extraction hop


async def test_url_text_extraction_refuses_internal_targets_before_any_request(
    monkeypatch,
):
    def unexpected(*args, **kwargs):
        raise AssertionError("the extraction API must not be called")

    monkeypatch.setattr(httpx.AsyncClient, "post", unexpected)

    with pytest.raises(PublicUrlError):
        await InputSourceService().fetch_from_url("http://169.254.169.254/latest/")
