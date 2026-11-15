"""Page screenshots (/screenshot)."""

import asyncio
import base64

from fastapi import HTTPException

from ...models.schemas import (
    ScreenshotRequest,
    ScreenshotResponse,
)
from ...utils.public_url import PublicUrlError
from ...services.screenshot_service import (
    ScreenshotError,
)
import logging


from fastapi import APIRouter

from .. import deps
from ..request_body import JsonObjectRoute
from ..responses import SAVE_NODE_ERROR_STATUS, upload_timeout_response
from .repository_docs import SCREENSHOT_RESPONSES

logger = logging.getLogger(__name__)

# The body is read like that of the raw-request endpoints (request_body.py).
router = APIRouter(route_class=JsonObjectRoute)


# ============================================================================
# Screenshot Endpoint
# ============================================================================


@router.post(
    "/screenshot",
    response_model=ScreenshotResponse,
    responses=SCREENSHOT_RESPONSES,
    summary="Screenshot einer Webseite erstellen",
    description="""Erstellt einen Screenshot einer Webseite.

## Methoden

| Methode | Datenschutz | Geschwindigkeit | Beschreibung |
|---------|-------------|-----------------|-------------|
| **playwright** | ✅ Intern, keine Daten nach außen | ~3-5s | Lokaler Chromium-Browser (Standard) |
| **pageshot** | ✅ wie playwright | wie playwright | Legacy-Alias — nimmt ebenfalls lokal auf; die Antwort meldet `playwright` |

Nur öffentliche http(s)-Ziele werden aufgenommen; interne Adressen (Loopback,
private Netze, Link-Local) beantwortet der Dienst mit HTTP 400.

## Optional: Preview-Upload

Wenn `node_id` angegeben wird, wird der Screenshot direkt als Vorschaubild auf den edu-sharing Node hochgeladen.
Das Bearbeitungsfenster (2 Stunden nach dem Anlegen) wird **vor** der Aufnahme
geprüft: `403`/`404` bzw. `502` bei einem Repository-Fehler, ohne dass ein Browser
startet. Aufnahme und Upload zusammen enden nach `METADATA_AGENT_UPLOAD_TIMEOUT`
Sekunden mit `504` (`UPLOAD_TIMEOUT`). Ohne Repository-Zugangsdaten antwortet
der Dienst mit `503`, ebenfalls vor der Aufnahme.

`success` heißt: die Aufnahme ist gelungen, das Bild steht in `image_base64`.
Ob es am Node gespeichert wurde, sagt `preview_uploaded`; lehnt das Repository
das Bild ab, bleibt es bei `200` mit `preview_uploaded: false` und dem Grund in
`error`.
""",
    tags=["Screenshot"],
)
async def take_screenshot(req: ScreenshotRequest):
    """Capture a screenshot and optionally upload as preview to edu-sharing node."""
    if not req.node_id:
        return await _capture_and_store(req)
    # With a node: capture, window check and upload together within
    # METADATA_AGENT_UPLOAD_TIMEOUT - they could otherwise outlast the 320 s
    # of ingress and grace period.
    try:
        async with asyncio.timeout(deps.settings.upload_timeout):
            return await _capture_and_store(req)
    except TimeoutError:
        return upload_timeout_response(
            deps.settings.upload_timeout,
            {
                "error": "Screenshot und Vorschau-Upload nicht rechtzeitig fertig",
                "node": {"nodeId": req.node_id},
            },
        )


async def _capture_and_store(req: ScreenshotRequest):
    screenshot_service = deps.get_screenshot_service()
    # With a node the repository is needed, as on every write path: 503 before
    # a browser starts for an image nobody could store (audit 14.11., R-10).
    repo_service = deps.require_repository_service() if req.node_id else None
    if repo_service:
        # Before the capture: a node outside its window must not hold a
        # browser slot for up to 90 s only to be refused afterwards.
        refusal = await repo_service.preview_refusal(req.node_id)
        if refusal:
            raise HTTPException(
                status_code=SAVE_NODE_ERROR_STATUS.get(
                    str(refusal.get("error_code")), 502
                ),
                detail=refusal.get("error"),
            )

    try:
        result = await screenshot_service.capture(
            url=req.url,
            method=req.method.value,
            width=req.width,
            height=req.height,
            format=req.format,
            full_page=req.full_page,
            delay=req.delay,
        )
    except PublicUrlError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ScreenshotError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        # The text can name internal hosts and paths: log it, do not return it.
        logger.error(f"❌ Screenshot failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Screenshot failed") from e

    response = ScreenshotResponse(
        success=True,
        method=result.method,
        url=result.url,
        format=result.format,
        mimetype=result.mimetype,
        width=result.width,
        height=result.height,
        size_bytes=result.size_bytes,
        capture_time_ms=result.capture_time_ms,
        image_base64=base64.b64encode(result.image_bytes).decode(),
    )

    # Optional: upload as preview to node
    if repo_service:
        # Upload the image just returned instead of capturing a second time;
        # the editing window was checked before the capture.
        upload_result = await repo_service.upload_preview(
            req.node_id, result.image_bytes, result.mimetype, window_checked=True
        )
        error_code = upload_result.get("error_code")
        if error_code in SAVE_NODE_ERROR_STATUS:
            raise HTTPException(
                status_code=SAVE_NODE_ERROR_STATUS[error_code],
                detail=upload_result.get("error"),
            )
        # Still a 200: the capture succeeded and the image is in the answer.
        response.preview_uploaded = bool(upload_result.get("success"))
        response.node_id = req.node_id
        if not response.preview_uploaded:
            response.error = (
                upload_result.get("error")
                or "Das Vorschaubild konnte nicht am Node gespeichert werden."
            )

    return response
