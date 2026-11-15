"""
Screenshots are taken locally. 'pageshot' survives only as a legacy alias.

The former default sent every URL to the external PageShot API. Old clients and
environment files still say 'pageshot'; they keep working, but their URLs must
never leave this service for a third party again.
"""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from src.config import Settings
from src.models.schemas import GenerateRequest, ScreenshotRequest, UploadRequest
from src.services import screenshot_service as module

URL = "https://example.org/"


@pytest.fixture
def public_url(monkeypatch):
    """Skip the DNS lookup; the URL policy has its own tests."""
    monkeypatch.setattr(module, "require_public_http_url", AsyncMock(return_value=URL))


@pytest.fixture
def no_outbound_http(monkeypatch):
    async def refuse(self, request, **kwargs):
        raise AssertionError(f"unexpected outbound request: {request.url}")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)


def test_screenshot_defaults_are_local_and_generation_still_opts_out():
    # The setting is gone (audit 14.11., O-45); the service's own fallback.
    assert module.ScreenshotService(Settings(_env_file=None)).default_method == (
        "playwright"
    )
    assert ScreenshotRequest(url=URL).method.value == "playwright"
    assert UploadRequest(metadata={}).screenshot_method.value == "playwright"
    assert UploadRequest(metadata={}, screenshot_method="").screenshot_method.value == (
        "playwright"
    )
    assert GenerateRequest(text="A resource").screenshot_method is None


def test_screenshot_defaults_use_a_full_hd_desktop_viewport():
    assert Settings.model_fields["screenshot_width"].default == 1920
    assert Settings.model_fields["screenshot_height"].default == 1080
    request = ScreenshotRequest(url=URL)
    assert (request.width, request.height) == (1920, 1080)
    assert request.full_page is False


def test_full_hd_defaults_remain_overridable(monkeypatch):
    monkeypatch.setenv("METADATA_AGENT_SCREENSHOT_WIDTH", "1280")
    monkeypatch.setenv("METADATA_AGENT_SCREENSHOT_HEIGHT", "720")
    service = module.ScreenshotService(Settings(_env_file=None))

    assert (service.default_width, service.default_height) == (1280, 720)


def test_the_external_pageshot_endpoint_is_no_longer_configurable():
    assert "pageshot_api_url" not in Settings.model_fields


@pytest.mark.parametrize(
    "requested, configured",
    [
        (None, "playwright"),
        ("playwright", "playwright"),
        ("pageshot", "playwright"),
        (None, "pageshot"),
    ],
)
async def test_legacy_and_current_methods_capture_locally(
    monkeypatch, public_url, no_outbound_http, requested, configured
):
    service = module.ScreenshotService(Settings(screenshot_method=configured))
    local = AsyncMock(
        return_value=module.ScreenshotResult(b"image", method="playwright")
    )
    monkeypatch.setattr(service, "_capture_playwright", local)

    await service.capture(URL, method=requested, width=640, height=480, delay=0)

    # What the real browser path reports is covered in
    # test_screenshot_browser_policy.py; here: every method takes that path.
    local.assert_awaited_once_with(URL, 640, 480, "png", False, 0)


async def test_a_local_failure_does_not_fall_back_to_an_external_service(
    monkeypatch, public_url, no_outbound_http
):
    service = module.ScreenshotService()
    monkeypatch.setattr(
        service,
        "_capture_playwright",
        AsyncMock(side_effect=module.ScreenshotError("Chromium unavailable")),
    )

    with pytest.raises(module.ScreenshotError, match="Chromium unavailable"):
        await service.capture(URL, method="pageshot")


async def test_an_unknown_method_is_refused_instead_of_guessed(monkeypatch, public_url):
    service = module.ScreenshotService()
    browser = AsyncMock(side_effect=AssertionError("browser must not start"))
    monkeypatch.setattr(service, "_capture_playwright", browser)

    with pytest.raises(module.ScreenshotError, match="Unsupported"):
        await service.capture(URL, method="something-else")

    browser.assert_not_awaited()


def test_formats_the_browser_cannot_produce_are_labelled_as_what_is_delivered():
    """Chromium writes PNG or JPEG only; a WebP request must not be labelled WebP."""
    assert module.delivered_format("webp") == "png"
    assert module.delivered_format("jpeg") == "jpeg"
    assert module.delivered_format("png") == "png"


async def test_only_a_few_browsers_run_at_once(monkeypatch, public_url):
    """Each capture starts its own Chromium; a burst must queue, not exhaust memory."""
    # The documented limit (docs/API.md "Limits and timeouts", DEPLOYMENT).
    assert module.MAX_PARALLEL_CAPTURES == 2
    service = module.ScreenshotService()
    active = peak = 0
    release = asyncio.Event()
    slots_full = asyncio.Event()

    async def capture(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == module.MAX_PARALLEL_CAPTURES:
            slots_full.set()
        try:
            await release.wait()
            return module.ScreenshotResult(b"image")
        finally:
            active -= 1

    monkeypatch.setattr(service, "_capture_playwright", capture)
    burst = module.MAX_PARALLEL_CAPTURES + 3
    tasks = [asyncio.create_task(service.capture(URL, delay=0)) for _ in range(burst)]
    await asyncio.wait_for(slots_full.wait(), 2)
    # Give every queued capture its turns on the loop - none may get a slot.
    for _ in range(10 * burst):
        await asyncio.sleep(0)
    assert peak == module.MAX_PARALLEL_CAPTURES
    release.set()
    results = await asyncio.gather(*tasks)
    assert peak == module.MAX_PARALLEL_CAPTURES
    assert len(results) == burst


async def test_a_hanging_capture_is_cut_off_at_the_deadline(monkeypatch, public_url):
    service = module.ScreenshotService()
    monkeypatch.setattr(module, "MAX_CAPTURE_SECONDS", 0.05)

    async def stuck(*args):
        await asyncio.sleep(5)

    monkeypatch.setattr(service, "_capture_playwright", stuck)

    with pytest.raises(module.ScreenshotError, match="timed out"):
        await service.capture(URL, delay=0)


async def test_waiting_for_a_free_browser_counts_against_the_deadline(
    monkeypatch, public_url
):
    service = module.ScreenshotService()
    started = 0
    all_slots_busy = asyncio.Event()
    release = asyncio.Event()

    async def capture(*args):
        nonlocal started
        started += 1
        if started == module.MAX_PARALLEL_CAPTURES:
            all_slots_busy.set()
        await release.wait()
        return module.ScreenshotResult(b"image")

    monkeypatch.setattr(service, "_capture_playwright", capture)
    running = [
        asyncio.create_task(service.capture(URL, delay=0))
        for _ in range(module.MAX_PARALLEL_CAPTURES)
    ]
    try:
        async with asyncio.timeout(1):
            await all_slots_busy.wait()
        # Only the queued capture gets the short deadline.
        monkeypatch.setattr(module, "MAX_CAPTURE_SECONDS", 0.05)

        with pytest.raises(module.ScreenshotError, match="timed out"):
            await service.capture(URL, delay=0)
        assert started == module.MAX_PARALLEL_CAPTURES
    finally:
        release.set()
        await asyncio.gather(*running)


async def test_a_slow_dns_check_counts_against_the_deadline(monkeypatch):
    service = module.ScreenshotService()
    monkeypatch.setattr(module, "MAX_CAPTURE_SECONDS", 0.05)

    async def slow_lookup(url):
        await asyncio.sleep(5)
        return url

    monkeypatch.setattr(module, "require_public_http_url", slow_lookup)
    browser = AsyncMock(side_effect=AssertionError("browser must not start"))
    monkeypatch.setattr(service, "_capture_playwright", browser)

    with pytest.raises(module.ScreenshotError, match="timed out"):
        await service.capture(URL, delay=0)

    browser.assert_not_awaited()
