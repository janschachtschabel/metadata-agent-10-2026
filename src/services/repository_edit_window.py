"""The editing window: how long after its creation a node may still be rewritten."""

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

import httpx

from .repository_support import node_created_at

logger = logging.getLogger(__name__)

# How long after its creation a node may still be rewritten through this service.
# Past that point the content has been in the editorial queue long enough for a
# human to have worked on it, and an automated write would replace that work
# without anyone being told. The window is deliberately short: it covers the
# session that produced the node, not later revisions.
NODE_EDIT_WINDOW = timedelta(hours=2)


class EditWindowMixin:
    """
    The check before a node is rewritten (part of RepositoryService).

    Needs NodeMixin, whose method it uses is declared below for mypy.
    """

    if TYPE_CHECKING:

        async def _read_node(
            self, client: httpx.AsyncClient, base_url: str, node_id: str
        ) -> tuple[Optional[dict[str, Any]], bool]: ...

    async def _reject_if_too_old(
        self, client: httpx.AsyncClient, base_url: str, node_id: str
    ) -> Optional[dict[str, Any]]:
        """
        Refuse to rewrite a node that has left its editing window.

        Returns the error result to answer with, or None when the node may be
        written. Fails closed: a node that cannot be read or cannot be dated is
        refused as well, because the guard protects editorial work this service
        has no other way of seeing.
        """
        refusal, _ = await self._edit_check(client, base_url, node_id)
        return refusal

    async def _edit_check(
        self, client: httpx.AsyncClient, base_url: str, node_id: str
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        """(refusal, None) or (None, the node as read) - see _reject_if_too_old."""
        node, missing = await self._read_node(client, base_url, node_id)
        node_info = {
            "nodeId": node_id,
            "repositoryUrl": f"{base_url}/components/render/{node_id}",
        }

        if node is None and not missing:
            # An outage says nothing about the node: not a 404 (502 instead).
            return {
                "success": False,
                "error_code": "node_unreadable",
                "error": (
                    f"Node {node_id} konnte wegen eines Repository-Fehlers nicht "
                    "gelesen werden — bitte später erneut versuchen."
                ),
                "node": node_info,
            }, None

        if node is None:
            return {
                "success": False,
                "error_code": "node_not_found",
                "error": (
                    f"Node {node_id} konnte nicht gelesen werden — er existiert "
                    "nicht oder der Dienst hat keinen Zugriff darauf."
                ),
                "node": node_info,
            }, None

        created = node_created_at(node)
        if created is None:
            return {
                "success": False,
                "error_code": "node_age_unknown",
                "error": (
                    f"Die Erstellungszeit von Node {node_id} ist nicht lesbar — "
                    "das Bearbeitungsfenster lässt sich nicht prüfen."
                ),
                "node": node_info,
                "node_full": node,
            }, None

        window_hours = NODE_EDIT_WINDOW.total_seconds() / 3600
        age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
        if age_hours > window_hours:
            logger.info(f"🔒 Node {node_id} is {age_hours:.1f}h old — edit refused")
            return {
                "success": False,
                "error_code": "node_too_old",
                "error": (
                    f"Node {node_id} wurde am {created.isoformat()} angelegt und "
                    f"ist {age_hours:.1f} Stunden alt. Bearbeitet werden kann nur "
                    f"innerhalb von {window_hours:.0f} Stunden nach dem Anlegen."
                ),
                "node": node_info,
                "node_full": node,
            }, None

        return None, node
