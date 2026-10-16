"""
Screenshots in a real Chromium: no path may smuggle an internal page into the result.

Playwright calls route handlers only for the first URL of a redirect chain, so a
public page can redirect — at once, while it settles, in a frame or for an image —
to an internal address. A test server stands in for both sides: 127.0.0.1 plays
the public host and 'localhost' the internal one. Skips without Chromium.
"""

import base64
import http.server
import socket
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from src import main
from src.services import screenshot_service as module
from src.utils.public_url import PublicUrlError

async_api = pytest.importorskip("playwright.async_api")

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0f01f0005fe02fea7d6a4f60000000049454e44ae426082"
)


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.seen.append((self.headers.get("Host", ""), self.path))
        internal = f"http://localhost:{self.server.server_address[1]}"
        html = {
            "/public": "<h1>public</h1>",
            "/internal": "<h1>internal</h1>",
            # Navigates after network idle, i.e. during the configured delay.
            "/later": "<script>setTimeout(() => location.href = '/redirect', 1200)</script>",
            "/frame": "<iframe src='/redirect'></iframe>",
            "/image": "<img src='/redirect-image'>",
            # Internal URLs requested directly, without a redirect.
            "/embeds-internal": (
                f"<h1>public</h1><img src='{internal}/pixel.png'>"
                f"<script>fetch('{internal}/internal').catch(() => {{}})</script>"
            ),
            # Navigations that fail after the page has loaded — while it
            # settles, and during the configured delay.
            "/navigates-away": (
                "<script>setTimeout(() => location.href = "
                f"'http://127.0.0.1:{self.server.closed_port}/', 100)</script>"
            ),
            "/navigates-away-later": (
                "<script>setTimeout(() => location.href = "
                f"'http://127.0.0.1:{self.server.closed_port}/', 1200)</script>"
            ),
        }
        redirects = {
            "/redirect": f"{internal}/internal",
            "/redirect-image": f"{internal}/pixel.png",
            # An internal port nobody listens on.
            "/redirect-closed": f"http://localhost:{self.server.closed_port}/",
        }
        if self.path in redirects:
            self.send_response(302)
            self.send_header("Location", redirects[self.path])
            self.end_headers()
        elif self.path == "/pixel.png":
            self._send(PNG_1PX, "image/png")
        elif self.path in html:
            self._send(html[self.path].encode(), "text/html")
        else:
            self.send_error(404)

    def _send(self, body: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    server.seen = []
    server.closed_port = _closed_port()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


@pytest.fixture
def site(server):
    server.seen.clear()
    return f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def policy(monkeypatch):
    """127.0.0.1 counts as public, 'localhost' as internal."""

    async def fake(url):
        if urlsplit(url).hostname == "localhost":
            raise PublicUrlError("Interne Hostnamen sind nicht erlaubt.")
        return url

    monkeypatch.setattr(module, "require_public_http_url", fake)


@pytest.fixture(scope="module")
def chromium_available():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Chromium missing: uv run playwright install chromium")


async def test_a_public_page_is_captured_locally(site, policy, chromium_available):
    result = await module.ScreenshotService().capture(
        f"{site}/public", method="pageshot", width=640, height=400, delay=0
    )

    assert result.method == "playwright"
    assert result.image_bytes.startswith(b"\x89PNG")


async def test_a_webp_request_is_delivered_and_labelled_as_png(
    site, policy, chromium_available
):
    result = await module.ScreenshotService().capture(
        f"{site}/public", format="webp", width=640, height=400, delay=0
    )

    assert result.image_bytes.startswith(b"\x89PNG")
    assert (result.format, result.mimetype) == ("png", "image/png")


def test_the_endpoint_labels_a_webp_request_as_the_png_it_returns(
    site, policy, chromium_available
):
    with TestClient(main.app) as client:
        response = client.post(
            "/screenshot",
            json={
                "url": f"{site}/public",
                "format": "webp",
                "width": 640,
                "height": 400,
                "delay": 0,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["format"], body["mimetype"]) == ("png", "image/png")
    assert base64.b64decode(body["image_base64"]).startswith(b"\x89PNG")


async def test_internal_subresources_are_never_requested(
    server, site, policy, chromium_available
):
    result = await module.ScreenshotService().capture(
        f"{site}/embeds-internal", width=640, height=400, delay=500
    )

    assert result.image_bytes.startswith(b"\x89PNG")
    assert [path for host, path in server.seen if host.startswith("localhost")] == []


async def test_a_failed_hop_inward_is_refused_like_a_successful_one(
    site, policy, chromium_available
):
    """Whether the internal port refused or answered must not show in the result."""
    with pytest.raises(PublicUrlError):
        await module.ScreenshotService().capture(
            f"{site}/redirect-closed", width=640, height=400, delay=0
        )


async def test_a_page_that_cannot_be_loaded_reports_no_browser_internals(
    server, policy, chromium_available
):
    with pytest.raises(module.ScreenshotError) as error:
        await module.ScreenshotService().capture(
            f"http://127.0.0.1:{server.closed_port}/", width=640, height=400, delay=0
        )

    assert "ERR_" not in str(error.value)
    assert "net::" not in str(error.value)


@pytest.mark.parametrize(
    "path, delay",
    [("/navigates-away", 0), ("/navigates-away-later", 2500)],
    ids=["while-settling", "during-delay"],
)
async def test_chromiums_error_page_is_a_load_failure_not_a_screenshot(
    site, policy, chromium_available, path, delay
):
    """A failed navigation leaves chrome-error:// behind — no URL the caller gave."""
    with pytest.raises(module.ScreenshotError, match="geladen"):
        await module.ScreenshotService().capture(
            f"{site}{path}", width=640, height=400, delay=delay
        )


@pytest.mark.parametrize(
    "path",
    ["/redirect", "/later", "/frame", "/image"],
    ids=["redirect", "navigation-while-settling", "frame-redirect", "image-redirect"],
)
async def test_no_route_to_an_internal_page_survives_the_capture(
    site, policy, chromium_available, path
):
    with pytest.raises(PublicUrlError):
        await module.ScreenshotService().capture(
            f"{site}{path}", width=640, height=400, delay=2500
        )
