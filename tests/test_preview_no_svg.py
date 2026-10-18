"""
No SVG as preview image (audit S-03).

An SVG is a document that can carry script. If edu-sharing ever serves the
stored original inline, an SVG preview is stored XSS on the repository origin.
Thumbnails need none of what SVG offers, so it is refused however it arrives:
declared by a data URL, served by a host, or hidden behind another type.
"""

import base64

import httpx
import pytest

from src.services.repository_preview import PreviewError, resolve_preview

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
XML_SVG = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"></svg>'
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _data_url(media: str, payload: bytes) -> str:
    return f"data:{media};base64,{base64.b64encode(payload).decode()}"


async def _never_called(request):
    raise AssertionError("no request expected")


@pytest.mark.parametrize(
    "value",
    [
        _data_url("image/svg+xml", SVG),
        _data_url("image/png", SVG),
        _data_url("image/png", XML_SVG),
        base64.b64encode(SVG).decode(),
    ],
)
async def test_an_inline_svg_is_refused(value):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_never_called)
    ) as client:
        with pytest.raises(PreviewError, match="SVG"):
            await resolve_preview(client, value)


@pytest.mark.parametrize(
    "content_type, body",
    [("image/svg+xml", SVG), ("image/png", SVG), ("application/octet-stream", XML_SVG)],
)
async def test_a_served_svg_is_refused(content_type, body):
    def handler(request):
        return httpx.Response(200, headers={"content-type": content_type}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PreviewError, match="SVG"):
            await resolve_preview(client, "http://93.184.216.34/image")


async def test_raster_images_still_pass():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_never_called)
    ) as client:
        image, mimetype = await resolve_preview(client, _data_url("image/png", PNG))

    assert (image, mimetype) == (PNG, "image/png")
