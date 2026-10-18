"""The preview image of an upload: decoding a data URL or fetching a public image URL safely."""

import base64
import logging
from typing import Optional
from urllib.parse import urljoin

import httpx

from ..config import Settings
from ..utils.public_url import PublicUrlError, pin_public_url

logger = logging.getLogger(__name__)


# Largest preview image this service will fetch or decode. edu-sharing renders a
# thumbnail from it, so nothing near this size is ever needed — the limit is
# there to stop a wrong URL from pulling an arbitrary download into memory.
MAX_PREVIEW_BYTES = 10 * 1024 * 1024


# Each hop of a preview redirect chain is checked; a longer chain is refused.
MAX_PREVIEW_REDIRECTS = 5


# Sent when fetching a preview image. Several of the hosts these images live on —
# Wikimedia among them — answer 403 to a client that identifies itself only by
# its HTTP library, so a name is not optional here. The version is the one
# /health reports (the settings default, kept in step with pyproject.toml).
PREVIEW_USER_AGENT = (
    f"MetadataAgentAPI/{Settings.model_fields['app_version'].default} (preview fetch)"
)


# Leading bytes of the image formats a preview may be (audit S-10: an allowlist).
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class PreviewError(Exception):
    """The value given as a preview could not be turned into an image."""


def _is_svg(image: bytes) -> bool:
    head = image.lstrip()[:1024].lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)


def _raster_image(image: bytes) -> tuple[bytes, str]:
    """
    The image with the media type its bytes show - PNG, JPEG, GIF or WebP only.

    What the data URL or the serving host claims does not count: a wrong label
    on a valid image is corrected, anything else is refused. SVG gets its own
    message - it is a document that can carry script, and served inline by the
    repository it would be stored XSS.
    """
    for signature, mimetype in _IMAGE_SIGNATURES:
        if image.startswith(signature):
            return image, mimetype
    if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return image, "image/webp"
    if _is_svg(image):
        raise PreviewError(
            "SVG wird als Vorschaubild nicht angenommen (PNG, JPEG, GIF oder WebP)."
        )
    raise PreviewError("Preview ist kein Bild im Format PNG, JPEG, GIF oder WebP.")


def _decode_preview_bytes(value: str) -> tuple[bytes, str]:
    """
    Decode an inline preview — a data URL or bare base64 — into image bytes.

    The media type comes from the decoded bytes, not from the data URL: a
    hand-built data URL routinely carries the wrong one.
    """
    payload = value

    if value[:5].lower() == "data:":
        _, _, payload = value.partition(",")
        if not payload:
            raise PreviewError("Data-URL ohne Bilddaten — nach dem Komma steht nichts.")

    try:
        image = base64.b64decode("".join(payload.split()), validate=True)
    except Exception as e:
        # The decoder's own wording adds nothing a caller can act on.
        logger.info(f"Preview not decodable: {type(e).__name__}: {e}")
        raise PreviewError(
            "Preview ist weder eine http(s)-URL noch gültiges Base64."
        ) from e

    if not image:
        raise PreviewError("Preview ist leer.")
    if len(image) > MAX_PREVIEW_BYTES:
        raise PreviewError(
            f"Preview ist {len(image) // 1024} KB groß — erlaubt sind "
            f"{MAX_PREVIEW_BYTES // 1024} KB."
        )

    return _raster_image(image)


async def resolve_preview(client: httpx.AsyncClient, value: str) -> tuple[bytes, str]:
    """
    Turn the `preview` parameter into image bytes and their media type.

    Two forms arrive here and are told apart by the value itself: an http(s)
    URL, which is fetched, and an inline image as a data URL or bare base64,
    which is decoded. Raises PreviewError with a message naming the form that
    was recognised, so a caller who meant the other one can see which.
    """
    candidate = value.strip()
    if not candidate:
        raise PreviewError("Preview ist leer.")

    lowered = candidate.lower()
    if not lowered.startswith(("http://", "https://")):
        if "://" in candidate[:20]:
            raise PreviewError(
                "Preview-URL muss mit http:// oder https:// beginnen — andere "
                "Schemas werden nicht geladen."
            )
        return _decode_preview_bytes(candidate)

    # Redirects are followed by hand: every hop has to be a public target, or a
    # public URL could bounce this service into an internal network.
    current_url = candidate
    for _ in range(MAX_PREVIEW_REDIRECTS + 1):
        next_url, image = await _fetch_hop(client, current_url)
        if next_url is None:
            break
        current_url = next_url
    else:
        raise PreviewError("Preview-URL leitet zu oft weiter.")

    if not image:
        raise PreviewError("Preview-URL lieferte keine Daten.")

    return _raster_image(image)


async def _fetch_hop(
    client: httpx.AsyncClient, url: str
) -> tuple[Optional[str], bytes]:
    """
    One request of the chain: (redirect target, b"") or (None, image bytes).

    Connects only to the addresses checked for the host (audit S-04), the next
    one when an address cannot be reached (e.g. IPv6 without IPv6 egress).
    """
    try:
        targets, host_header, extensions = await pin_public_url(url)
    except PublicUrlError as e:
        raise PreviewError(f"Preview-URL ist nicht öffentlich: {e}") from e

    unreachable: Optional[Exception] = None
    for target in targets:
        try:
            async with client.stream(
                "GET",
                target,
                headers={
                    **host_header,
                    "Accept": "image/*",
                    # The size limit counts bytes as they arrive (see
                    # _read_preview_body); a compressed answer is refused.
                    "Accept-Encoding": "identity",
                    "User-Agent": PREVIEW_USER_AGENT,
                },
                extensions=extensions,
                follow_redirects=False,
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    return _redirect_target(url, response), b""
                if response.status_code != 200:
                    raise PreviewError(
                        "Preview-Bild konnte nicht geladen werden: "
                        f"HTTP {response.status_code}"
                    )
                return None, await _read_preview_body(response)
        except httpx.ConnectError as e:
            unreachable = e
            continue
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            # InvalidURL is not an HTTPError subclass. The text can name
            # addresses: log it, tell the caller the type (audit S-06).
            logger.warning(f"Preview fetch failed: {type(e).__name__}: {e}")
            raise PreviewError(
                f"Preview-Bild konnte nicht geladen werden ({type(e).__name__})"
            ) from e
    logger.warning(
        f"Preview host unreachable: {type(unreachable).__name__}: {unreachable}"
    )
    raise PreviewError(
        f"Preview-Bild konnte nicht geladen werden ({type(unreachable).__name__})"
    )


def _redirect_target(url: str, response: httpx.Response) -> str:
    """The Location of a redirect, resolved against the URL that was asked for."""
    location = response.headers.get("location")
    if not location:
        raise PreviewError("Preview-Weiterleitung ohne Ziel.")
    try:
        return urljoin(url, location)
    except ValueError as e:
        # httpx accepts some targets urllib cannot parse.
        raise PreviewError(
            f"Preview-Weiterleitung mit ungültigem Ziel: {location!r}"
        ) from e


async def _read_preview_body(response: httpx.Response) -> bytes:
    """
    Read an image response, refusing it as soon as it exceeds the limit.

    httpx decodes a Content-Encoding before the bytes reach this loop, so one
    chunk of stacked gzip could inflate far past the limit before it is
    counted. A compressed answer is therefore refused — the request asks for
    identity — and the bytes counted are the bytes received.
    """
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding not in ("", "identity"):
        raise PreviewError(
            f"Preview-Bild kam komprimiert ({encoding}) — unterstützt wird nur "
            "eine unkomprimierte Antwort."
        )

    too_large = (
        "Preview-Bild ist größer als erlaubt — höchstens "
        f"{MAX_PREVIEW_BYTES // 1024} KB."
    )
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_PREVIEW_BYTES:
        raise PreviewError(too_large)

    image = bytearray()
    async for chunk in response.aiter_bytes():
        image.extend(chunk)
        if len(image) > MAX_PREVIEW_BYTES:
            raise PreviewError(too_large)
    return bytes(image)
