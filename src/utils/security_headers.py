"""
Basic security headers on every response (audit S-12) - and the one request
header the API refuses: the marker of its own screenshot browser (S-15).
"""

import json
import logging
import secrets

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .request_id import current_request_id

logger = logging.getLogger(__name__)

# nosniff: a browser never runs an answer as script or HTML that is labelled as
# something else (JSON, a stored preview). no-referrer: the API pages and the
# widget examples link to third parties without telling them where from.
# No X-Frame-Options / frame-ancestors: the widget pages are meant to be embedded.
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]

# Chromium adds this header to every request of a screenshot, redirect hops
# included (screenshot_service.py). A page being captured cannot remove it, so
# whatever it makes the browser send to the agent's own API - over loopback,
# through a redirect the URL check never saw - arrives marked. The value carries
# no meaning: the presence alone is refused, so the check also holds when
# several worker processes share a port.
SCREENSHOT_BROWSER_HEADER = "X-Metadata-Agent-Screenshot"
SCREENSHOT_BROWSER_TOKEN = secrets.token_hex(8)
_MARKER = SCREENSHOT_BROWSER_HEADER.lower().encode("ascii")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                names = {name.lower() for name, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    header for header in SECURITY_HEADERS if header[0] not in names
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)


class ScreenshotBrowserRefusalMiddleware:
    """403 for every request that carries the screenshot browser's marker."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not any(
            name.lower() == _MARKER for name, _ in scope.get("headers", [])
        ):
            await self.app(scope, receive, send)
            return

        logger.warning(
            "Refused a request of the screenshot browser: %s %s",
            scope.get("method"),
            # Percent-decoded: %0A must not start a forged log line.
            str(scope.get("path", "")).replace("\r", " ").replace("\n", " "),
        )
        detail = (
            "Anfragen des Screenshot-Browsers an den Agent selbst sind nicht erlaubt."
        )
        # The API's error shape (responses.py), built here: utils import no api.
        body = json.dumps(
            {
                "detail": detail,
                "error": {
                    "code": "SCREENSHOT_BROWSER_REFUSED",
                    "message": detail,
                    "request_id": current_request_id(),
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
