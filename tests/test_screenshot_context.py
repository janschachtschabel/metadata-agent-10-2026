"""
How the screenshot browser's context is set up - without starting a browser.

Audit 13.11., S-21: Playwright accepts downloads unless told otherwise. A
captured page could start downloads that fill /tmp (an emptyDir with a size
limit, or a tmpfs in RAM) until the pod is evicted.
"""

import pytest

from src.services import screenshot_service as screenshot_module
from src.services.screenshot_service import ScreenshotError, ScreenshotService


class _Stop(Exception):
    """Ends the capture once the context options are known."""


def _fake_playwright(seen: dict):
    class _Browser:
        async def new_context(self, **options):
            seen.update(options)
            raise _Stop()

        async def close(self):
            pass

    class _Chromium:
        async def launch(self, **options):
            return _Browser()

    class _Playwright:
        chromium = _Chromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    return _Playwright


@pytest.mark.asyncio
async def test_the_browser_context_accepts_no_downloads(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr("playwright.async_api.async_playwright", _fake_playwright(seen))

    async def public(url):
        return url

    monkeypatch.setattr(screenshot_module, "require_public_http_url", public)

    with pytest.raises((_Stop, ScreenshotError)):
        await ScreenshotService().capture("https://example.org/")

    assert seen["accept_downloads"] is False


# ------------------------------------------------- answers to a page's POSTs
#
# Audit 13.11., S-21: route.fetch() buffers the whole answer in the driver. The
# page decides how large it is and how long it takes.


class _Answer:
    status = 200

    def __init__(self, headers, body=b"ok"):
        self.headers = headers
        self._body = body

    async def body(self):
        return self._body


class _Route:
    def __init__(self, answer):
        self.request = type("R", (), {"url": "https://example.org/post"})()
        self.answer = answer
        self.fetched_with: dict = {}
        self.outcome = None

    async def fetch(self, **options):
        self.fetched_with = options
        return self.answer

    async def fulfill(self, **options):
        self.outcome = "fulfilled"

    async def abort(self, reason=None):
        self.outcome = f"aborted:{reason}"


async def _allowed(url):
    return True


@pytest.mark.asyncio
async def test_an_oversized_answer_to_a_post_is_not_handed_to_the_page():
    too_large = str(screenshot_module.MAX_POST_ANSWER_BYTES + 1)
    route = _Route(_Answer({"content-length": too_large}))

    await screenshot_module._one_hop_at_a_time(route, _allowed, [])

    assert route.outcome == "aborted:blockedbyclient"


@pytest.mark.asyncio
async def test_an_ordinary_answer_to_a_post_still_reaches_the_page():
    route = _Route(_Answer({"content-length": "1200"}))

    await screenshot_module._one_hop_at_a_time(route, _allowed, [])

    assert route.outcome == "fulfilled"
    # No redirect is followed, and the wait for the answer is bounded.
    assert route.fetched_with["max_redirects"] == 0
    assert route.fetched_with["timeout"] == screenshot_module.POST_ANSWER_TIMEOUT_MS


# Audit 14.11., S-41: a chunked answer announces no length, and a compressed
# one announces the smaller one. The body itself is measured as well.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [{}, {"transfer-encoding": "chunked"}, {"content-length": "1200"}],
    ids=["no length", "chunked", "smaller length announced"],
)
async def test_an_answer_that_is_larger_than_it_says_is_not_handed_to_the_page(headers):
    body = b"x" * (screenshot_module.MAX_POST_ANSWER_BYTES + 1)
    route = _Route(_Answer(headers, body))

    await screenshot_module._one_hop_at_a_time(route, _allowed, [])

    assert route.outcome == "aborted:blockedbyclient"


@pytest.mark.asyncio
async def test_an_answer_of_exactly_the_limit_reaches_the_page():
    body = b"x" * screenshot_module.MAX_POST_ANSWER_BYTES
    route = _Route(_Answer({"transfer-encoding": "chunked"}, body))

    await screenshot_module._one_hop_at_a_time(route, _allowed, [])

    assert route.outcome == "fulfilled"
