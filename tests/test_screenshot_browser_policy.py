"""
Screenshots in a real Chromium: no path may smuggle an internal page into the result.

Playwright calls route handlers only for the first URL of a redirect chain, so a
public page can redirect — at once, while it settles, in a frame or for an image —
to an internal address. A test server stands in for both sides: 127.0.0.1 plays
the public host and 'localhost' the internal one. Skips without Chromium.
"""

import base64
import contextlib
import http.server
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import uvicorn
from browser_requirement import browser_missing
from fastapi.testclient import TestClient

from src import main
from src.services import screenshot_service as module
from src.utils.public_url import PublicUrlError

async_api = pytest.importorskip("playwright.async_api")

NODE_ID = "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"

# A direct link to a large file (video, PDF, ZIP) as the page to capture.
BIG_BYTES = 50 * 1024 * 1024
BIG_FILES = {"/big.mp4": "video/mp4", "/big.zip": "application/zip"}

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
            # A form POST answered with a 307 to an internal address.
            "/post-inward": (
                "<form id=f method=POST enctype=text/plain action='/redirect-inward'>"
                "<input name=a value=b></form>"
                "<script>document.getElementById('f').submit()</script>"
            ),
        }
        redirects = {
            "/redirect": f"{internal}/internal",
            "/redirect-image": f"{internal}/pixel.png",
            # An internal port nobody listens on.
            "/redirect-closed": f"http://localhost:{self.server.closed_port}/",
        }
        if self.headers.get("Upgrade", "").lower() == "websocket":
            # Recorded above; nobody may get this far for an internal host.
            self.send_error(400)
        elif self.path in redirects:
            self.send_response(302)
            self.send_header("Location", redirects[self.path])
            self.end_headers()
        elif self.path == "/to-api":
            # A request for an image and a text/plain form POST - the body any
            # page can send without CORS - both redirected to the agent's own
            # API on loopback (the "public" test host).
            self._send(
                (
                    "<img src='/redirect-api'>"
                    "<form id=f method=POST enctype=text/plain action='/redirect-api'>"
                    '<input name=\'{"steps":["150_PUBLISH_IN_SEARCH"],"x":"\' '
                    "value='\"}'></form>"
                    "<script>setTimeout(() => document.getElementById('f').submit(), 300)"
                    "</script>"
                ).encode(),
                "text/html",
            )
        elif self.path in ("/redirect-api", "/navigate-to-api"):
            self.send_response(302)
            self.send_header("Location", f"{self.server.api}/health")
            self.end_headers()
        elif self.path in BIG_FILES:
            self._stream(BIG_FILES[self.path])
        elif self.path == "/cross-origin-fetch":
            other = self.server.other_origin
            self._send(
                (
                    f"<script>fetch('{other}/data').then(r => r.text())"
                    f".then(t => fetch('{other}/result?' + t))</script>"
                ).encode(),
                "text/html",
            )
        elif self.path == "/redirect-public":
            self.send_response(302)
            self.send_header("Location", "/sub/relative")
            self.end_headers()
        elif self.path == "/sub/relative":
            # Resolves against the final URL only: /sub/pixel.png.
            self._send(b"<h1>final</h1><img src='pixel.png'>", "text/html")
        elif self.path == "/sub/pixel.png":
            self._send(PNG_1PX, "image/png")
        elif self.path == "/websocket":
            self._send(
                (
                    "<script>new WebSocket('ws://localhost:"
                    f"{self.server.server_address[1]}/socket')</script>"
                ).encode(),
                "text/html",
            )
        elif self.path == "/pixel.png":
            self._send(PNG_1PX, "image/png")
        elif self.path in html:
            self._send(html[self.path].encode(), "text/html")
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.server.seen.append((self.headers.get("Host", ""), f"POST {self.path}"))
        targets = {
            "/redirect-api": f"{self.server.api}/workflow/{NODE_ID}",
            "/redirect-inward": (
                f"http://localhost:{self.server.server_address[1]}/internal"
            ),
        }
        if self.path in targets:
            self.send_response(307)
            self.send_header("Location", targets[self.path])
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(404)

    def _stream(self, content_type: str):
        """BIG_BYTES, chunked and without a length, as a file server streams them."""
        self.protocol_version = "HTTP/1.1"
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        chunk = b"\0" * 65536
        try:
            for _ in range(BIG_BYTES // len(chunk)):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.server.streamed += len(chunk)
            self.wfile.write(b"0\r\n\r\n")
        except OSError:
            pass  # the browser stopped reading and went away

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
    server.streamed = 0
    server.other_origin = ""
    server.closed_port = _closed_port()
    server.api = f"http://127.0.0.1:{server.closed_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


class _ThreadServer(uvicorn.Server):
    """uvicorn in a thread: the signals stay with the test process."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


@pytest.fixture(scope="module")
def agent_api(server):
    """
    The real app on a loopback port, as in the pod - every answer recorded.

    127.0.0.1 counts as public in these tests, so only the app itself can
    refuse what the screenshot browser sends it.
    """
    answered: list[tuple[str, str, int]] = []

    async def recording(scope, receive, send):
        async def record(message):
            if message["type"] == "http.response.start":
                answered.append((scope["method"], scope["path"], message["status"]))
            await send(message)

        await main.app(scope, receive, record)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    api = _ThreadServer(
        uvicorn.Config(
            recording, lifespan="off", log_config=None, access_log=False, ws="none"
        )
    )
    thread = threading.Thread(target=api.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not api.started:
        time.sleep(0.05)
    server.api = f"http://127.0.0.1:{sock.getsockname()[1]}"
    yield answered
    api.should_exit = True
    thread.join(5)
    server.api = f"http://127.0.0.1:{server.closed_port}"


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
            browser_missing("Chromium missing: uv run playwright install chromium")


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


async def test_the_agents_own_api_refuses_every_request_of_the_screenshot_browser(
    site, policy, chromium_available, agent_api
):
    """
    Audit S-15: a redirect hop reached the API on loopback, a text/plain POST
    included. Every request of our Chromium carries a marker - redirect hops
    too - and the API refuses it, whatever the URL check let through.
    """
    agent_api.clear()

    with contextlib.suppress(PublicUrlError, module.ScreenshotError):
        await module.ScreenshotService().capture(
            f"{site}/to-api", width=640, height=400, delay=1500
        )

    assert agent_api, "the redirect hops never reached the API"
    assert [method for method, _, _ in agent_api if method != "GET"] == []
    assert {status for _, _, status in agent_api} == {403}


async def test_a_form_post_redirected_inward_is_never_sent(
    server, site, policy, chromium_available
):
    """Navigation hops are checked before Chromium sends them, not after."""
    with pytest.raises(PublicUrlError):
        await module.ScreenshotService().capture(
            f"{site}/post-inward", width=640, height=400, delay=1500
        )

    assert [path for host, path in server.seen if host.startswith("localhost")] == []


async def test_a_redirect_to_a_public_page_lands_on_its_url(
    server, site, policy, chromium_available
):
    """Relative links of the final page resolve against its own URL."""
    result = await module.ScreenshotService().capture(
        f"{site}/redirect-public", width=640, height=400, delay=0
    )

    assert result.image_bytes.startswith(b"\x89PNG")
    assert "/sub/pixel.png" in [path for _, path in server.seen]


async def test_a_websocket_to_an_internal_host_is_never_opened(
    server, site, policy, chromium_available
):
    """Audit S-09, in a real browser: route() never sees WebSockets."""
    await module.ScreenshotService().capture(
        f"{site}/websocket", width=640, height=400, delay=1000
    )

    assert "/websocket" in [path for _, path in server.seen]
    assert [path for host, path in server.seen if host.startswith("localhost")] == []


@pytest.mark.parametrize("path", sorted(BIG_FILES))
async def test_a_large_file_as_the_page_is_not_downloaded_whole(
    server, site, policy, chromium_available, path
):
    """
    Review 18.10.: route.fetch() buffered every navigation in the driver - a
    60 MB video was downloaded completely, then page.goto timed out. Chromium
    streams a GET navigation itself and stops reading what it cannot show.
    """
    server.streamed = 0
    start = time.monotonic()

    with contextlib.suppress(module.ScreenshotError):
        await module.ScreenshotService().capture(
            f"{site}{path}", width=640, height=400, delay=0
        )

    assert time.monotonic() - start < 25
    assert server.streamed < BIG_BYTES // 2, f"{server.streamed} bytes sent"


@pytest.fixture
def api_is_internal(monkeypatch, server, agent_api):
    """As in the pod: the agent's own API on loopback is no public target."""

    async def fake(url):
        parts = urlsplit(url)
        if parts.hostname == "localhost" or parts.netloc == urlsplit(server.api).netloc:
            raise PublicUrlError("Loopback ist nicht erlaubt.")
        return url

    monkeypatch.setattr(module, "require_public_http_url", fake)


async def test_a_navigation_redirected_to_the_api_reaches_no_endpoint_and_is_refused(
    site, chromium_available, agent_api, api_is_internal
):
    """
    A GET navigation follows its redirects in Chromium: the hop reaches the API
    (marker header: 403 before any endpoint runs) and the capture is refused.
    """
    agent_api.clear()

    with pytest.raises(PublicUrlError):
        await module.ScreenshotService().capture(
            f"{site}/navigate-to-api", width=640, height=400, delay=0
        )

    assert agent_api == [("GET", "/health", 403)]


class _OtherOrigin(http.server.BaseHTTPRequestHandler):
    """Allows every origin to read, but refuses a CORS preflight as strict servers do."""

    def do_GET(self):
        self.server.seen.append(("GET", self.path, dict(self.headers)))
        body = b"ok"
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.server.seen.append(("OPTIONS", self.path, dict(self.headers)))
        self.send_error(405)

    def log_message(self, *args):
        pass


@pytest.fixture
def other_origin(server):
    other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _OtherOrigin)
    other.seen = []
    threading.Thread(target=other.serve_forever, daemon=True).start()
    server.other_origin = f"http://127.0.0.1:{other.server_address[1]}"
    yield other
    other.shutdown()
    server.other_origin = ""


async def test_the_marker_header_triggers_no_cors_preflight(
    site, policy, chromium_available, other_origin
):
    """
    Review 18.10.: the marker is added below the page (extra HTTP headers), so a
    cross-origin fetch() stays a simple request - no OPTIONS a strict server
    could refuse.
    """
    await module.ScreenshotService().capture(
        f"{site}/cross-origin-fetch", width=640, height=400, delay=1000
    )

    requests = [(method, path) for method, path, _ in other_origin.seen]
    assert ("GET", "/data") in requests
    assert ("GET", "/result?ok") in requests  # the page could read the answer
    assert "OPTIONS" not in [method for method, _ in requests]
    marked = [h for m, p, h in other_origin.seen if p == "/data"][0]
    assert module.SCREENSHOT_BROWSER_HEADER.lower() in {k.lower() for k in marked}
