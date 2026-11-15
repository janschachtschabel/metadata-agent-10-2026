"""Screenshot service for capturing webpage previews.

Screenshots are rendered locally with Playwright (Chromium in this service).
'pageshot' is still accepted as a method name for old clients and environment
files, but it no longer sends URLs to the external PageShot API — it captures
locally as well.

The captured screenshots can be uploaded as preview images to edu-sharing nodes.
"""

import asyncio
import html
import logging
import time
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit
from weakref import WeakKeyDictionary

from ..config import get_settings
from ..utils.loggable import loggable_text, loggable_url
from ..utils.public_url import PublicUrlError, require_public_http_url
from ..utils.security_headers import (
    SCREENSHOT_BROWSER_HEADER,
    SCREENSHOT_BROWSER_TOKEN,
)

logger = logging.getLogger(__name__)

# 'pageshot' is the legacy name of the former external default.
LOCAL_METHODS = ("playwright", "pageshot")

# Every capture starts its own Chromium. Bound how many run at once and how long
# one may take including the wait for a slot, so a burst queues instead of
# exhausting the container's memory.
MAX_PARALLEL_CAPTURES = 2
MAX_CAPTURE_SECONDS = 90

# Keyed by event loop: a semaphore binds to the loop it is first awaited in —
# one loop in production, one per test.
_capture_slots: "WeakKeyDictionary[Any, asyncio.Semaphore]" = WeakKeyDictionary()


def _capture_slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slot = _capture_slots.get(loop)
    if slot is None:
        slot = _capture_slots[loop] = asyncio.Semaphore(MAX_PARALLEL_CAPTURES)
    return slot


def delivered_format(requested: str) -> str:
    """Chromium writes PNG or JPEG only; anything else is delivered as PNG."""
    return requested if requested in ("png", "jpeg") else "png"


# A request of these methods carries no body that a redirect could resend.
_SAFE_METHODS = ("GET", "HEAD")


def _refresh_page(target: str) -> str:
    """A page that navigates on to `target` - as a new, routed navigation."""
    url = html.escape(target, quote=True)
    return f'<!doctype html><meta http-equiv="refresh" content="0;url={url}">'


def _public_requests_only(redirect_hops: list[str]):
    """
    Route handler that aborts browser requests to non-public hosts.

    A public page may embed internal URLs. Each distinct origin is checked once
    per capture; data:, blob: and about: URLs need no network. Playwright calls
    route handlers only for the first URL of a redirect chain: requests with a
    body therefore go out one hop at a time (_one_hop_at_a_time). GET and HEAD -
    navigations included - stay with Chromium, which streams them (a direct link
    to a large video or ZIP is not downloaded whole); their redirect targets
    land in `redirect_hops` and refuse the capture afterwards (see
    _require_public_targets), and the API refuses the marked hop itself.
    """
    verdicts: dict[str, bool] = {}

    async def allowed(url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in verdicts:
            try:
                await require_public_http_url(url)
                verdicts[origin] = True
            except PublicUrlError as e:
                logger.warning(f"Blocked browser request to {loggable_url(url)}: {e}")
                verdicts[origin] = False
        return verdicts[origin]

    async def handle(route):
        request = route.request
        if urlsplit(request.url).scheme in ("data", "blob", "about"):
            await route.continue_()
        elif not await allowed(request.url):
            await route.abort("blockedbyclient")
        elif request.method not in _SAFE_METHODS:
            await _one_hop_at_a_time(route, allowed, redirect_hops)
        else:
            await route.continue_()

    return handle


# Answers to requests with a body that a page being captured sends itself.
MAX_POST_ANSWER_BYTES = 5 * 1024 * 1024
POST_ANSWER_TIMEOUT_MS = 15_000


def _announced_length(response) -> int:
    try:
        return int(response.headers.get("content-length", 0))
    except ValueError:
        return 0


async def _too_large(response) -> bool:
    """
    An answer over MAX_POST_ANSWER_BYTES - by the length it announces, else by
    its body. A chunked answer announces none, a compressed one the smaller
    one: both passed the check of the header alone (audit 14.11., S-41).
    """
    if _announced_length(response) > MAX_POST_ANSWER_BYTES:
        return True
    return len(await response.body()) > MAX_POST_ANSWER_BYTES


async def _one_hop_at_a_time(route, allowed, redirect_hops: list[str]) -> None:
    """
    Send a request with a body without following a redirect; check the target.

    Chromium follows a redirect without asking the route handler again (audit
    S-15: a 307 carried a text/plain POST to the API on loopback). So no 3xx
    reaches it: a hop of a form submission becomes a new GET navigation, which
    passes the handler again, and any other request with a body is not
    redirected at all - a 307/308 would resend the body to the new target.
    route.fetch() buffers the answer in the driver; bodies of answers to POSTs
    are small, unlike a direct link to a file (see _public_requests_only). The
    page decides that, though: the wait is bounded, and an answer larger than
    MAX_POST_ANSWER_BYTES is not copied into the page as well (audit 13.11.,
    S-21; _too_large).
    """
    from playwright.async_api import Error as PlaywrightError

    request = route.request
    try:
        response = await route.fetch(max_redirects=0, timeout=POST_ANSWER_TIMEOUT_MS)
    except PlaywrightError as e:
        logger.info(
            f"📸 {loggable_url(request.url)} could not be fetched: {loggable_text(e)}"
        )
        await route.abort("failed")
        return
    if await _too_large(response):
        logger.info(f"📸 {loggable_url(request.url)}: answer to a POST too large")
        await route.abort("blockedbyclient")
        return
    location = response.headers.get("location")
    if not (300 <= response.status < 400 and location):
        await route.fulfill(response=response)
        return
    target = urljoin(request.url, location)
    redirect_hops.append(target)
    if request.is_navigation_request() and await allowed(target):
        await route.fulfill(
            status=200, content_type="text/html", body=_refresh_page(target)
        )
    else:
        await route.abort("blockedbyclient")


def _public_websockets_only():
    """
    WebSocket handler that connects only to public hosts.

    route() never sees WebSockets: without this, a page could open ws:// to an
    internal address from inside the pod (audit S-09). A refused socket is
    closed without ever reaching a server.
    """

    async def handle(ws):
        http_url = "http" + ws.url[len("ws") :] if ws.url.startswith("ws") else ws.url
        try:
            await require_public_http_url(http_url)
        except PublicUrlError as e:
            logger.warning(f"Blocked browser WebSocket to {loggable_url(ws.url)}: {e}")
            await ws.close(code=1008, reason="blocked")
            return
        ws.connect_to_server()

    return handle


async def _require_public_targets(urls) -> None:
    """Raise PublicUrlError unless every http(s) URL is public (once per origin)."""
    checked: set[str] = set()
    for url in urls:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if parts.scheme not in ("http", "https") or origin in checked:
            continue
        checked.add(origin)
        try:
            await require_public_http_url(url)
        except PublicUrlError as e:
            raise PublicUrlError(
                f"Die Seite führte zu einem nicht öffentlichen Ziel: {e}"
            ) from e


_LOAD_FAILED = "Die Seite konnte nicht geladen werden."


def _require_loaded(page_url: str) -> None:
    """A navigation that failed leaves Chromium's error page (chrome-error://)."""
    if urlsplit(page_url).scheme not in ("http", "https"):
        raise ScreenshotError(_LOAD_FAILED)


async def _open(page, url: str, redirect_hops: list[str]) -> None:
    """Navigate and let the page settle; refuse what led inward or did not load."""
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except PlaywrightError as e:
        # A hop inward is refused first: whether the internal target refused
        # the connection or answered must not show in the response.
        await _require_public_targets(redirect_hops)
        logger.info(f"📸 {loggable_url(url)} could not be loaded: {loggable_text(e)}")
        raise ScreenshotError(_LOAD_FAILED) from e

    # Give the network a bounded chance to settle. Pages that never go quiet
    # (analytics, long polling) are captured anyway instead of failing.
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        logger.info(
            f"📸 {loggable_url(url)} did not reach network idle — capturing anyway"
        )

    await _require_public_targets(redirect_hops)
    _require_loaded(page.url)
    # Redirects must not have ended on an internal address.
    await require_public_http_url(page.url)


class ScreenshotResult:
    """Result of a screenshot capture."""

    def __init__(
        self,
        image_bytes: bytes,
        format: str = "png",
        width: int = 0,
        height: int = 0,
        capture_time_ms: int = 0,
        method: str = "playwright",
        url: str = "",
    ):
        self.image_bytes = image_bytes
        self.format = format
        self.width = width
        self.height = height
        self.capture_time_ms = capture_time_ms
        self.method = method
        self.url = url
        self.size_bytes = len(image_bytes)

    @property
    def mimetype(self) -> str:
        return f"image/{self.format}"

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "url": self.url,
            "format": self.format,
            "mimetype": self.mimetype,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "capture_time_ms": self.capture_time_ms,
        }


class ScreenshotService:
    """Service for capturing screenshots of webpages."""

    def __init__(self, settings=None):
        if settings is None:
            settings = get_settings()

        self.default_method = LOCAL_METHODS[0]
        self.default_width = settings.screenshot_width
        self.default_height = settings.screenshot_height
        self.default_format = settings.screenshot_format
        self.full_page = settings.screenshot_full_page
        self.delay = settings.screenshot_delay

    async def capture(
        self,
        url: str,
        method: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        format: Optional[str] = None,
        full_page: Optional[bool] = None,
        delay: Optional[int] = None,
    ) -> ScreenshotResult:
        """
        Capture a screenshot of a URL.

        Args:
            url: Webpage URL to capture
            method: 'playwright' or the legacy alias 'pageshot' (default from config)
            width: Viewport width (default from config)
            height: Viewport height (default from config)
            format: 'png' or 'jpeg'; anything else is delivered as png (default from config)
            full_page: Capture full scrollable page (default from config)
            delay: Wait before capture in ms (default from config)

        Returns:
            ScreenshotResult with image bytes and metadata

        Raises:
            PublicUrlError: the URL, a redirect or a frame led to a non-public target
            ScreenshotError: the page could not be loaded or captured in time;
                the message never carries browser internals
        """
        method = method or self.default_method
        if method not in LOCAL_METHODS:
            raise ScreenshotError(f"Unsupported screenshot method: {method}")
        width = width or self.default_width
        height = height or self.default_height
        format = format or self.default_format
        full_page = full_page if full_page is not None else self.full_page
        delay = delay if delay is not None else self.delay

        try:
            async with asyncio.timeout(MAX_CAPTURE_SECONDS):
                # Refuse internal targets before a browser starts.
                url = await require_public_http_url(url)
                async with _capture_slot():
                    return await self._capture_playwright(
                        url, width, height, format, full_page, delay
                    )
        except TimeoutError as e:
            raise ScreenshotError("Screenshot capture timed out") from e

    async def _capture_playwright(
        self,
        url: str,
        width: int,
        height: int,
        format: str,
        full_page: bool,
        delay: int,
    ) -> ScreenshotResult:
        """Capture screenshot using internal Playwright (privacy-safe)."""
        start = time.monotonic()
        logger.info(f"📸 Playwright: capturing {loggable_url(url)} ({width}x{height})")

        try:
            from playwright.async_api import Error as PlaywrightError
            from playwright.async_api import async_playwright
        except ImportError:
            raise ScreenshotError(
                "Playwright is not installed. Install with: pip install playwright && playwright install chromium"
            )

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    context = await browser.new_context(
                        viewport={"width": width, "height": height},
                        device_scale_factor=1,
                        # Service workers fetch outside the request check below.
                        service_workers="block",
                        # A page could fill /tmp with downloads it starts itself
                        # until the pod is evicted (audit 13.11., S-21).
                        accept_downloads=False,
                        # On every request, redirect hops included: the API
                        # refuses it (security_headers.py, audit S-15).
                        extra_http_headers={
                            SCREENSHOT_BROWSER_HEADER: SCREENSHOT_BROWSER_TOKEN
                        },
                    )
                    # Redirect targets the route handler did not send itself
                    # (images, scripts) reach the network unchecked. Collect
                    # them so the result can be refused.
                    redirect_hops: list[str] = []
                    await context.route("**/*", _public_requests_only(redirect_hops))
                    await context.route_web_socket("**/*", _public_websockets_only())

                    def note_redirect(request):
                        if request.redirected_from is not None:
                            redirect_hops.append(request.url)

                    context.on("request", note_redirect)
                    page = await context.new_page()
                    await _open(page, url, redirect_hops)

                    # Additional delay if configured
                    if delay > 0:
                        await page.wait_for_timeout(delay)

                    image_format = delivered_format(format)
                    image_bytes = await page.screenshot(
                        type="jpeg" if image_format == "jpeg" else "png",
                        full_page=full_page,
                        quality=85 if image_format == "jpeg" else None,
                    )

                    # The page may have redirected — in a frame, for an image,
                    # or while it settled. Refuse the result if anything led
                    # inward, or if it navigated away and failed meanwhile.
                    await _require_public_targets(
                        [*redirect_hops, *(frame.url for frame in page.frames)]
                    )
                    _require_loaded(page.url)
                finally:
                    await browser.close()
        except PlaywrightError as e:
            # Browser messages carry network errors and server paths: they stay
            # in the log, the caller gets a neutral reason.
            logger.error(
                f"❌ Playwright: capturing {loggable_url(url)} failed: {loggable_text(e)}"
            )
            raise ScreenshotError("Screenshot konnte nicht erstellt werden.") from e

        capture_time = int((time.monotonic() - start) * 1000)
        logger.info(f"✅ Playwright: {len(image_bytes)} bytes ({capture_time}ms)")

        return ScreenshotResult(
            image_bytes=image_bytes,
            format=image_format,
            width=width,
            height=height,
            capture_time_ms=capture_time,
            method="playwright",
            url=url,
        )


class ScreenshotError(Exception):
    """Raised when screenshot capture fails."""

    pass


# Singleton
_screenshot_service: Optional[ScreenshotService] = None


def get_screenshot_service() -> ScreenshotService:
    """Get screenshot service singleton."""
    global _screenshot_service
    if _screenshot_service is None:
        _screenshot_service = ScreenshotService()
    return _screenshot_service
