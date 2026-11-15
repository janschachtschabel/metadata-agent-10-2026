"""Checking what reached the repository and setting a node's workflow status."""

import logging
from typing import TYPE_CHECKING, Any, Optional

import httpx

from ..metrics import OWN_SERVICE_HOOKS
from ..utils.tls_context import shared_tls_context
from .repository_curation import (
    fetch_workflow_history,
    run_workflow_steps,
)
from .repository_diff import compute_diff, properties_to_flat, unresolved_values
from .repository_support import get_repository_config, repository_error, sent_record

logger = logging.getLogger(__name__)


class ReviewMixin:
    """
    Verification and workflow (part of RepositoryService).

    Needs _auth_header (RepositoryService.__init__) and NodeMixin, whose method
    it uses is declared below for mypy.
    """

    _auth_header: str

    if TYPE_CHECKING:

        async def _fetch_full_node(
            self, client: httpx.AsyncClient, base_url: str, node_id: str
        ) -> Optional[dict[str, Any]]: ...

        async def _read_node(
            self, client: httpx.AsyncClient, base_url: str, node_id: str
        ) -> tuple[Optional[dict[str, Any]], bool]: ...

    async def verify_node(
        self,
        node_id: str,
        repository: str = "staging",
        expected_metadata: dict[str, Any] | None = None,
        context: str = "default",
        version: str = "latest",
    ) -> dict[str, Any]:
        """
        Read metadata from repository and optionally compare against expected values.

        Args:
            node_id: The node ID to verify
            repository: 'staging' or 'prod'
            expected_metadata: If provided, compute field-level diff
            context: Schema context for repo_field filtering
            version: Schema version for repo_field filtering

        Returns:
            Dict with actual_metadata, optional diff and summary
        """
        config = get_repository_config()

        base_url = config["base_url"]

        # Read like the write path reads a body: flat or nested, the markers
        # where the form has them (audit 14.11., R-07). The router looks for
        # context and version beside `metadata` only.
        if expected_metadata:
            expected_metadata = sent_record(expected_metadata)
            named_context = expected_metadata.get("contextName")
            named_version = expected_metadata.get("schemaVersion")
            if isinstance(named_context, str) and named_context:
                context = named_context
            if isinstance(named_version, str) and named_version:
                version = named_version

        try:
            timeout = httpx.Timeout(30.0, connect=10.0)
            async with httpx.AsyncClient(
                timeout=timeout,
                event_hooks=OWN_SERVICE_HOOKS,
                verify=shared_tls_context(),
            ) as client:
                # Fetch node metadata
                url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/metadata?propertyFilter=-all-"
                response = await client.get(
                    url,
                    headers={
                        "Authorization": self._auth_header,
                        "Accept": "application/json",
                    },
                )

                if response.status_code != 200:
                    # 403 too: the service account cannot see the node, which
                    # the caller cannot tell from a missing one (as on /nodes).
                    return {
                        "success": False,
                        **(
                            {"error_code": "node_not_found"}
                            if response.status_code in (403, 404)
                            else {}
                        ),
                        "error": f"Failed to fetch node: {repository_error(response)}",
                    }

                data = response.json()
                properties = data.get("node", {}).get("properties", {})

                # Convert to flat metadata (same logic as input_source_service)
                actual = properties_to_flat(properties)

                result = {
                    "success": True,
                    "node_id": node_id,
                    "actual_metadata": actual,
                }

                # Which of the node's values the repository does not read as
                # values. Needs no expected metadata: a field can hold exactly
                # what was sent and still resolve to nothing, and that is
                # invisible in every other part of this answer.
                schema_file = (expected_metadata or {}).get("metadataset")
                result["unresolved"] = unresolved_values(
                    properties, context, version, schema_file
                )

                # If expected metadata provided, compute diff
                if expected_metadata:
                    diff, summary = compute_diff(
                        expected_metadata, actual, context, version, properties
                    )
                    result["diff"] = diff
                    result["summary"] = summary

                return result

        # Exception texts can name internal hosts: the log gets them, the
        # answer the type (audit S-06).
        except httpx.TimeoutException as e:
            logger.error(f"❌ Verify of {node_id} timed out: {type(e).__name__}: {e}")
            return {"success": False, "error": f"Timeout ({type(e).__name__})"}
        except Exception as e:
            logger.error(f"❌ Verify of {node_id} failed: {type(e).__name__}: {e}")
            return {
                "success": False,
                "error": f"Node konnte nicht geprüft werden ({type(e).__name__})",
            }

    async def set_workflow(
        self,
        node_id: str,
        steps: list[str],
        comment: Optional[str] = None,
        receiver: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Advance an existing node through the review workflow.

        Used after the upload has already handed the node over for human
        checking, to walk the remaining editorial states step by step.

        Returns the per-step result plus the node's resulting workflow state and
        its full history (which records who set which state). A node the
        repository does not show this account (403, 404) gets error_code
        node_not_found before any state is set.
        """
        config = get_repository_config()
        base_url = config["base_url"]

        try:
            timeout = httpx.Timeout(45.0, connect=10.0)
            async with httpx.AsyncClient(
                timeout=timeout,
                event_hooks=OWN_SERVICE_HOOKS,
                verify=shared_tls_context(),
            ) as client:
                # An outage on this read says nothing about the node: the
                # steps then run and report their own failures, as before.
                _, missing = await self._read_node(client, base_url, node_id)
                if missing:
                    return {
                        "success": False,
                        "error_code": "node_not_found",
                        "nodeId": node_id,
                        "error": (
                            f"Node {node_id} konnte nicht gelesen werden — er "
                            "existiert nicht oder der Dienst hat keinen Zugriff darauf."
                        ),
                    }
                run = await run_workflow_steps(
                    client,
                    self._auth_header,
                    base_url,
                    node_id,
                    steps,
                    comment,
                    receiver,
                )

                result: dict[str, Any] = {
                    "success": run["success"],
                    "nodeId": node_id,
                    "steps": run["steps"],
                    "repositoryUrl": f"{base_url}/components/render/{node_id}",
                }

                # Read back so callers see the state that actually stuck
                node = await self._fetch_full_node(client, base_url, node_id)
                if node:
                    wf_status = (node.get("properties") or {}).get("ccm:wf_status")
                    if isinstance(wf_status, list):
                        wf_status = wf_status[0] if wf_status else None
                    result["current_status"] = wf_status

                history = await fetch_workflow_history(
                    client, self._auth_header, base_url, node_id
                )
                if history is not None:
                    result["history"] = history

                if not run["success"]:
                    failed = [s["status"] for s in run["steps"] if not s["success"]]
                    result["error"] = (
                        f"Workflow-Schritt(e) fehlgeschlagen: {', '.join(failed)}"
                    )

                return result

        except Exception as e:
            # The text can name internal hosts: log it, answer with the type.
            logger.error(f"❌ Workflow of {node_id} failed: {type(e).__name__}: {e}")
            if isinstance(e, httpx.TimeoutException):
                cause = "Timeout bei der Verbindung zum Repository"
            elif isinstance(e, httpx.ConnectError):
                cause = "Verbindung zum Repository fehlgeschlagen"
            else:
                cause = "Workflow konnte nicht gesetzt werden"
            return {
                "success": False,
                "nodeId": node_id,
                "error": f"{cause} ({type(e).__name__})",
            }


# Singleton instance
