"""Basic security headers on every response (audit S-12)."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# nosniff: a browser never runs an answer as script or HTML that is labelled as
# something else (JSON, a stored preview). no-referrer: the API pages and the
# widget examples link to third parties without telling them where from.
# No X-Frame-Options / frame-ancestors: the widget pages are meant to be embedded.
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]


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
