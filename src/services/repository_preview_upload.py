"""Storing a preview image on a node: on its own (/screenshot) and as a write step."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional
from urllib.parse import quote

import httpx

from ..metrics import OWN_SERVICE_HOOKS, THIRD_PARTY_HOOKS
from ..utils.tls_context import shared_tls_context
from .repository_preview import PreviewError, resolve_preview
from .repository_support import get_repository_config, repository_error
from .repository_write import PreviewSource

logger = logging.getLogger(__name__)


# Seconds of the write deadline the preview may not use: the requests that
# follow it (one PUT per collection, one per workflow state) still have to fit.
# A capture may take up to 90 s, the deadline defaults to 100 s - without the
# reserve a slow page ends the write at the deadline, during the collections
# or the workflow. The preview is not worth that. This much for the first
# request that follows ...
PREVIEW_RESERVE_SECONDS = 15.0
# ... and this for every further one, up to the maximum: the preview keeps
# more than half of the default deadline whatever was asked for.
PREVIEW_RESERVE_STEP_SECONDS = 5.0
PREVIEW_RESERVE_MAX_SECONDS = 45.0
# With no request after it the preview is the last step inside the deadline;
# this margin keeps its own limit ahead of the deadline, so the preview is
# what gets cut, not the write.
PREVIEW_MARGIN_SECONDS = 1.0


def preview_reserve(collections: int, workflow_steps: int) -> float:
    """Seconds the preview leaves for the requests after it (audit 14.11., R-02)."""
    following = collections + workflow_steps
    if following == 0:
        return PREVIEW_MARGIN_SECONDS
    return min(
        PREVIEW_RESERVE_MAX_SECONDS,
        PREVIEW_RESERVE_SECONDS + PREVIEW_RESERVE_STEP_SECONDS * (following - 1),
    )


def fetched_preview(preview: str) -> PreviewSource:
    """
    An image source for a URL or inline image data (data URL, bare base64).

    A URL is fetched with a client of its own: the host is a third party and
    gets no tracing headers.
    """

    async def fetch() -> tuple[bytes, str]:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            event_hooks=THIRD_PARTY_HOOKS,
            verify=shared_tls_context(),
        ) as fetch_client:
            return await resolve_preview(fetch_client, preview)

    return fetch


class PreviewUploadMixin:
    """
    Preview images (part of RepositoryService).

    Needs _auth_header (RepositoryService.__init__) and EditWindowMixin, whose
    method it uses is declared below for mypy.
    """

    _auth_header: str

    if TYPE_CHECKING:

        async def _reject_if_too_old(
            self, client: httpx.AsyncClient, base_url: str, node_id: str
        ) -> Optional[dict[str, Any]]: ...

    async def preview_refusal(self, node_id: str) -> Optional[dict[str, Any]]:
        """
        The refusal upload_preview would answer for this node, or None.

        For /screenshot, which checks before it starts the browser: a node
        outside its window must not hold one of the few capture slots.
        """
        base_url = get_repository_config()["base_url"]
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=10.0),
            event_hooks=OWN_SERVICE_HOOKS,
            verify=shared_tls_context(),
        ) as client:
            return await self._reject_if_too_old(client, base_url, node_id)

    async def upload_preview(
        self, node_id: str, image: bytes, mimetype: str, window_checked: bool = False
    ) -> dict[str, Any]:
        """
        Store an image as preview of an existing node - inside its editing window.

        The window protects editorial work like it does for /upload and /nodes:
        a published node keeps its preview. Returns the refusal (error_code
        node_too_old, node_age_unknown, node_not_found or node_unreadable) or
        the upload result; never raises. `window_checked`: preview_refusal ran
        just before, the window is not read a second time.
        """
        base_url = get_repository_config()["base_url"]
        timeout = httpx.Timeout(45.0, connect=10.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            event_hooks=OWN_SERVICE_HOOKS,
            verify=shared_tls_context(),
        ) as client:
            if not window_checked:
                refusal = await self._reject_if_too_old(client, base_url, node_id)
                if refusal:
                    return refusal
            return await self._post_preview(client, base_url, node_id, image, mimetype)

    async def _bounded_preview(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str,
        preview_source: PreviewSource,
        deadline: Optional[float],
        reserve: float,
    ) -> dict[str, Any]:
        """
        The preview within what the deadline leaves after `reserve` seconds.

        Out of time it is reported as failed and the write goes on: the node
        holds its metadata already, and a missing picture is not worth a 504
        that discards it.
        """
        if deadline is None:
            return await self._preview_from_source(
                client, base_url, node_id, preview_source
            )
        budget = deadline - asyncio.get_running_loop().time() - reserve
        unavailable = {
            "success": False,
            "error": "Vorschaubild nicht rechtzeitig verfügbar — ohne Vorschau "
            "fortgefahren, damit das Zeitlimit für die folgenden Schritte "
            "(Sammlungen, Workflow) reicht",
        }
        if budget <= 0:
            logger.warning("⚠️ Preview skipped: no time left for the steps after it")
            return unavailable
        try:
            async with asyncio.timeout(budget):
                return await self._preview_from_source(
                    client, base_url, node_id, preview_source
                )
        except TimeoutError:
            logger.warning(f"⚠️ Preview given up after {budget:.1f} s")
            return unavailable

    async def _preview_from_source(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str,
        preview_source: Callable[[], Awaitable[tuple[bytes, str]]],
    ) -> dict[str, Any]:
        """Await an image source and store the image; failures become the result."""
        try:
            image, mimetype = await preview_source()
        except PreviewError as e:
            logger.warning(f"⚠️ Preview rejected: {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            # The text can name internal hosts: log it, answer with the type.
            logger.warning(f"⚠️ Preview not available: {type(e).__name__}: {e}")
            return {
                "success": False,
                "error": f"Vorschaubild nicht verfügbar ({type(e).__name__})",
            }
        return await self._post_preview(client, base_url, node_id, image, mimetype)

    def _fetched_preview(self, preview: str) -> PreviewSource:
        """fetched_preview as a method, as before the routers built the write."""
        return fetched_preview(preview)

    async def _post_preview(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str,
        image: bytes,
        mimetype: str,
    ) -> dict[str, Any]:
        """Upload image bytes as the node's preview; never raises."""
        extension = mimetype.rsplit("/", 1)[-1]
        url = (
            f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/preview"
            f"?mimetype={quote(mimetype, safe='')}&createVersion=true"
        )

        try:
            response = await client.post(
                url,
                headers={"Authorization": self._auth_header},
                files={"image": (f"preview.{extension}", image, mimetype)},
            )
        except Exception as e:
            # The text can name internal hosts: log it, answer with the type.
            logger.warning(f"⚠️ Preview upload failed: {type(e).__name__}: {e}")
            return {
                "success": False,
                "error": f"Upload des Vorschaubilds fehlgeschlagen ({type(e).__name__})",
            }

        if response.status_code in (200, 201, 204):
            logger.info(f"🖼️ Preview set on {node_id} ({mimetype}, {len(image)} bytes)")
            return {
                "success": True,
                "mimetype": mimetype,
                "size_bytes": len(image),
            }

        logger.warning(f"⚠️ Preview upload failed: HTTP {response.status_code}")
        return {
            "success": False,
            "error": repository_error(response),
        }
