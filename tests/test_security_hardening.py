"""
Hardening from the audit of 2026-10-18 (S-04, S-06, S-08, S-09, S-10, S-12).

- S-04: the preview fetch resolved the host twice - once to check it, once to
  connect. A second DNS answer (rebinding) could point the connection inward.
- S-06: repository error bodies (with Java stack traces), the address of the
  AI gateway and internal hosts reached the client.
- S-08: /health on the public port named provider and auth mode.
- S-09: Playwright's route() does not see WebSockets; a page could open one to
  an internal host from inside the pod.
- S-10: the preview took any 200 body as image/png; only SVG was refused.
- S-12: no X-Content-Type-Options / Referrer-Policy on responses.
"""

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src import main
from src.api import deps
from src.services.repository_preview import PreviewError, resolve_preview
from src.services.repository_support import repository_error
from src.services.screenshot_service import _public_websockets_only
from src.utils import public_url

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8


# ------------------------------------------------------------- S-10 / S-04


@pytest.mark.parametrize(
    "body, content_type",
    [
        (b"<html><script>alert(1)</script></html>", "image/png"),
        (b"%PDF-1.7 ...", "application/pdf"),
        (b"just text", "image/jpeg"),
    ],
)
async def test_a_served_body_that_is_no_raster_image_is_refused(body, content_type):
    def handler(request):
        return httpx.Response(200, headers={"content-type": content_type}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PreviewError, match="PNG, JPEG, GIF oder WebP"):
            await resolve_preview(client, "http://93.184.216.34/image")


@pytest.mark.parametrize(
    "image, expected", [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")]
)
async def test_the_bytes_decide_the_type_not_the_label(image, expected):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/gif"}, content=image)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        _, mimetype = await resolve_preview(client, "http://93.184.216.34/image")

    assert mimetype == expected


async def test_inline_data_that_is_no_image_is_refused():
    value = "data:image/png;base64," + base64.b64encode(b"<html></html>").decode()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    ) as client:
        with pytest.raises(PreviewError, match="PNG, JPEG, GIF oder WebP"):
            await resolve_preview(client, value)


async def test_the_preview_connects_to_the_address_that_was_checked(monkeypatch):
    lookups = []

    async def resolve(hostname, port):
        lookups.append(hostname)
        # First answer public, any later one internal: a rebinding attacker.
        return ["93.184.216.34"] if len(lookups) == 1 else ["10.0.0.5"]

    monkeypatch.setattr(public_url, "resolve_public_host", resolve)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await resolve_preview(client, "https://images.example.org/bild.png")

    assert lookups == ["images.example.org"], "resolved once, not again to connect"
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "images.example.org"
    assert seen[0].extensions["sni_hostname"] == "images.example.org"


# ------------------------------------------------------------------- S-06


class _Answer:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)
        self._body = body

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("no json")
        return self._body


def test_a_repository_error_keeps_its_message_and_drops_the_stack_trace():
    answer = _Answer(
        500,
        {
            "error": "org.edu_sharing.restservices.DAOException",
            "message": "Node not readable",
            "stacktraceArray": ["at org.alfresco.repo.Foo(Foo.java:12)"],
        },
    )

    assert repository_error(answer) == "HTTP 500: Node not readable"


def test_a_repository_error_without_json_shows_only_the_status():
    answer = _Answer(502, "<html>Bad Gateway nginx/1.25 at 10.1.2.3</html>")

    assert repository_error(answer) == "HTTP 502"


def test_a_failed_fetch_names_no_internal_host(monkeypatch):
    class _Broken:
        async def fetch_from_url(self, *args, **kwargs):
            raise RuntimeError(
                "connect to http://text-extraction.svc.cluster.local:8080 failed"
            )

    monkeypatch.setattr(deps, "get_input_source_service", lambda: _Broken())

    with TestClient(main.app) as client:
        response = client.post(
            "/detect-content-type",
            json={"input_source": "url", "source_url": "https://example.org/kurs"},
        )

    assert response.status_code == 502
    assert "cluster.local" not in response.text
    assert "RuntimeError" in response.text


# ------------------------------------------------------------------- S-08


def test_public_health_names_no_configuration():
    with TestClient(main.app) as client:
        body = client.get("/health").json()

    assert body == {"status": "healthy", "version": deps.settings.app_version}


# ------------------------------------------------------------------- S-12


def test_responses_carry_the_basic_security_headers():
    with TestClient(main.app) as client:
        response = client.get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


# ------------------------------------------------------------------- S-09


class _Socket:
    def __init__(self, url):
        self.url = url
        self.connected = False
        self.closed = None

    def connect_to_server(self):
        self.connected = True

    async def close(self, code=None, reason=None):
        self.closed = (code, reason)


@pytest.mark.parametrize(
    "url", ["ws://127.0.0.1:9000/x", "wss://10.0.0.5/socket", "ws://localhost/"]
)
async def test_a_websocket_to_an_internal_host_is_closed(url):
    socket = _Socket(url)

    await _public_websockets_only()(socket)

    assert not socket.connected
    assert socket.closed is not None


async def test_a_websocket_to_a_public_host_is_connected():
    socket = _Socket("wss://93.184.216.34/live")

    await _public_websockets_only()(socket)

    assert socket.connected
    assert socket.closed is None


# The capture routes WebSockets through the check: a real browser proves it in
# test_screenshot_browser_policy.py (test_a_websocket_to_an_internal_host_...).


async def test_the_pinned_fetch_prefers_ipv4_and_falls_back(monkeypatch):
    # Review finding 1: addresses sorted as strings put IPv6 (and 3.x-9.x)
    # first; without IPv6 egress the fetch failed where httpx used to fall back.
    async def resolve(hostname, port):
        return sorted(["2606:2800:220:1::1", "93.184.216.34", "54.230.1.1"])

    monkeypatch.setattr(public_url, "resolve_public_host", resolve)
    tried = []

    def handler(request):
        tried.append(request.url.host)
        if request.url.host == "54.230.1.1":
            raise httpx.ConnectError("unreachable")
        return httpx.Response(200, content=PNG)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await resolve_preview(client, "https://images.example.org/bild.png")

    assert tried == ["54.230.1.1", "93.184.216.34"], "IPv4 first, next on failure"
