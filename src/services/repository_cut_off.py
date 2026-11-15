"""
What becomes of the node when a write does not finish: cut off at its deadline
or by a client that went away, failed half-way, or the create answer was lost.
"""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from .repository_result import WRITE_FAILED, created_result
from .repository_write import NodeWrite, WriteProgress, WriteTarget

logger = logging.getLogger(__name__)


def _linked_summary(collections: list[dict[str, Any]]) -> str:
    """'verknüpft: a; nicht verknüpft: b (reason)' for the text of a cut-off write."""
    linked = [c["collectionId"] for c in collections if c["success"]]
    parts = [f"verknüpft: {', '.join(linked) if linked else 'keine'}"]
    not_linked = [
        f"{c['collectionId']} ({c.get('error')})"
        for c in collections
        if not c["success"]
    ]
    if not_linked:
        parts.append(f"nicht verknüpft: {', '.join(not_linked)}")
    return "; ".join(parts)


class CutOffMixin:
    """
    Cleanup and answers of an unfinished write (part of RepositoryService).

    Needs NodeMixin, whose methods it uses are declared below for mypy.
    """

    if TYPE_CHECKING:

        async def _discard_node(self, base_url: str, node_id: str) -> bool: ...

        async def _find_lost_node(
            self,
            base_url: str,
            inbox_id: str,
            clean_metadata: dict,
            created_after: datetime,
            repo_field_ids: Any = None,
        ) -> Optional[str]: ...

    async def _create_cut_off(
        self, base_url: str, target: WriteTarget, progress: WriteProgress
    ) -> dict[str, Any]:
        """The answer for a POST /node its deadline ended; a node found is named."""
        node_id = progress.created_node_id
        if node_id is None and progress.creating_since is not None:
            node_id = await self._find_lost_node(
                base_url,
                target.inbox_id,
                target.clean_metadata,
                progress.creating_since,
                target.repo_field_ids,
            )
        result: dict[str, Any] = {"success": False, "error_code": "upload_timeout"}
        if node_id:
            result["node"] = created_result(base_url, node_id, target.clean_metadata)[
                "node"
            ]
            note = f"Node {node_id} wurde trotzdem angelegt"
        elif progress.creating_since is not None:
            note = (
                "möglicherweise wurde trotzdem ein Node angelegt; bitte den "
                "Eingangsordner prüfen"
            )
        else:
            note = "es wurde kein Node angelegt"
        result["error"] = f"Zeitlimit beim Anlegen des Nodes erreicht — {note}"
        return result

    async def _write_failed(
        self, base_url: str, job: NodeWrite, progress: WriteProgress, error: str
    ) -> dict[str, Any]:
        """
        The answer for a write that raised; a node already kept is not discarded.

        A kept node is named in `node`, also one the caller handed over. The
        discard is shielded: a cancel arriving meanwhile must not abort it.
        """
        node_id = progress.created_node_id
        if job.node_id and progress.node_touched and not progress.keeps_node:
            # The request may have reached the repository before it raised
            # (e.g. ReadTimeout): said like the deadline path says it.
            return {
                "success": False,
                "error": f"{error} — der bestehende Node {job.node_id} kann "
                "teilweise geschrieben sein",
                "node": {
                    "nodeId": job.node_id,
                    "repositoryUrl": f"{base_url}/components/render/{job.node_id}",
                },
            }
        if progress.keeps_node and node_id is None:
            node_id = job.node_id
        return await asyncio.shield(
            self._failed(
                base_url,
                node_id,
                error,
                maybe_created=progress.maybe_created,
                keep=progress.keeps_node,
            )
        )

    async def _clean_up_cut_off(
        self, base_url: str, target: WriteTarget, progress: WriteProgress
    ) -> Optional[dict[str, Any]]:
        """
        Undo what a cut-off write created, and say what happened to the node.

        Returns the node as the answer names it: discarded, handed back for
        cleanup, or kept because it already went into the workflow (the
        editors may have been notified) or a collection may reference it
        (then with `collections`). None when no node was touched.
        """
        node_id = progress.created_node_id
        if node_id is None and progress.creating_since is not None:
            node_id = await self._find_lost_node(
                base_url,
                target.inbox_id,
                target.clean_metadata,
                progress.creating_since,
                target.repo_field_ids,
            )
        if node_id is None:
            return None
        info = {
            "nodeId": node_id,
            "repositoryUrl": f"{base_url}/components/render/{node_id}",
        }
        if progress.workflow_started or progress.finished:
            where = "und im Workflow" if progress.workflow_started else "vollständig"
            return {
                "node": info,
                "note": f"Node {node_id} ist geschrieben {where}; "
                "er wurde nicht verworfen",
            }
        if progress.keeps_node:
            collections = progress.collections_at_cut_off()
            return {
                "node": info,
                "collections": collections,
                "note": f"Node {node_id} ist geschrieben, die Verknüpfung mit den "
                f"Sammlungen wurde unterbrochen ({_linked_summary(collections)}); "
                "er wurde nicht verworfen",
            }
        logger.warning(f"⏱️ Write cut off; discarding node {node_id}")
        if await self._discard_node(base_url, node_id):
            return {
                "discarded_node": node_id,
                "note": "der dabei angelegte Node wurde verworfen",
            }
        return {
            "node": info,
            "note": f"der unvollständige Node {node_id} konnte nicht verworfen "
            "werden und liegt weiterhin im Eingangsordner",
        }

    async def _cut_off_result(
        self,
        base_url: str,
        job: NodeWrite,
        target: WriteTarget,
        progress: WriteProgress,
    ) -> dict[str, Any]:
        """The answer for a write its deadline ended (audit D-04)."""
        outcome = await self._clean_up_cut_off(base_url, target, progress)
        result: dict[str, Any] = {"success": False, "error_code": "upload_timeout"}
        if outcome:
            note = outcome.pop("note")
            result.update(outcome)
        elif job.node_id:
            note = (
                f"der bestehende Node {job.node_id} kann teilweise geschrieben sein"
                if progress.node_touched
                else f"der bestehende Node {job.node_id} wurde nicht verändert"
            )
            result["node"] = {"nodeId": job.node_id}
            if progress.collection_results is not None and not (
                progress.workflow_started or progress.finished
            ):
                result["collections"] = progress.collections_at_cut_off()
                note += f" (Sammlungen {_linked_summary(result['collections'])})"
        elif progress.creating_since is not None:
            note = (
                "möglicherweise wurde trotzdem ein Node angelegt; bitte den "
                "Eingangsordner prüfen"
            )
        else:
            note = "es wurde kein Node angelegt"
        if "collections" in result and job.workflow_steps:
            note += "; der Workflow wurde nicht gestartet"
        result["error"] = f"Zeitlimit beim Schreiben ins Repository erreicht — {note}"
        return result

    async def _metadata_rejected(
        self, base_url: str, created_node_id: Optional[str], metadata_result: dict
    ) -> dict[str, Any]:
        """Answer for a metadata write the repository refused entirely."""
        result = await self._failed(
            base_url,
            created_node_id,
            "Das Repository hat keines der Metadatenfelder angenommen",
        )
        result["step"] = "setMetadata"
        result["field_errors"] = metadata_result.get("field_errors", [])
        return result

    async def _failed(
        self,
        base_url: str,
        created_node_id: Optional[str],
        error: str,
        maybe_created: bool = False,
        keep: bool = False,
    ) -> dict[str, Any]:
        """
        Build the error response and undo a half-finished upload.

        The upload is create-then-populate. When the second half never runs, the
        node stays in the inbox carrying only its title — invisible to the
        caller, who sees success=false and retries, leaving one more behind
        every time. Discarding it keeps a failed upload from becoming litter.

        `keep`: the node is complete or already in the workflow (editors may
        have been notified) - it is reported in `node`, never discarded. Only
        with `keep` may the id be a node the caller handed over. Everything
        else left nothing behind but litter: error_code WRITE_FAILED (502).
        """
        result: dict[str, Any] = {
            "success": False,
            "error_code": WRITE_FAILED,
            "error": error,
        }
        if created_node_id and keep:
            del result["error_code"]
            result["node"] = {
                "nodeId": created_node_id,
                "repositoryUrl": f"{base_url}/components/render/{created_node_id}",
            }
            result["error"] = (
                f"{error} — Node {created_node_id} ist bereits geschrieben und "
                "wurde nicht verworfen"
            )
            return result
        if not created_node_id:
            if maybe_created:
                # The answer to "create" got lost and no new node turned up.
                result["error"] = (
                    f"{error} — möglicherweise wurde trotzdem ein Node angelegt; "
                    "bitte den Eingangsordner prüfen"
                )
            return result

        if await self._discard_node(base_url, created_node_id):
            result["discarded_node"] = created_node_id
            result["error"] = f"{error} (unvollständiger Node wurde verworfen)"
        else:
            result["node"] = {"nodeId": created_node_id}
            result["error"] = (
                f"{error} — der unvollständige Node {created_node_id} konnte nicht "
                "verworfen werden und liegt weiterhin im Eingangsordner"
            )
        return result
