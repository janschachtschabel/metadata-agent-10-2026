"""
What happens to a node after its metadata is written.

Two curation steps that follow the upload but change for their own reasons:
referencing the content in collections, and walking it through the WLO editorial
review workflow one state at a time.

The functions take an open HTTP client and the auth header rather than holding
state — `repository_service` owns the connection.
"""

from typing import Any, Optional

import httpx
import logging

from .repository_support import repository_error

logger = logging.getLogger(__name__)

# Defined next to the request models that check them; re-exported, as callers
# and tests import them from here.
from ..models.repository_rules import (  # noqa: E402
    DEFAULT_WORKFLOW_RECEIVER as DEFAULT_WORKFLOW_RECEIVER,
    DEFAULT_WORKFLOW_STATUS as DEFAULT_WORKFLOW_STATUS,
    KNOWN_WORKFLOW_STATUS as KNOWN_WORKFLOW_STATUS,
    QUALITY_WORKFLOW_STATUS as QUALITY_WORKFLOW_STATUS,
    extract_id_from_url as extract_id_from_url,
    is_valid_node_id as is_valid_node_id,
)


def extract_collection_ids(
    metadata: dict, extra: Optional[list[str]] = None
) -> list[str]:
    """
    Collect the collection IDs a node should be referenced in.

    Sources, in this order: the explicitly requested IDs (upload parameter),
    the primary collection from metadata, additional collections from
    metadata. Duplicates are removed while keeping the first occurrence.
    """
    ids = []

    # Explicitly requested collections (upload parameter)
    for coll in extra or []:
        ids.append(extract_id_from_url(coll))

    # Primary collection
    primary = metadata.get("virtual:collection_id_primary")
    if primary:
        ids.append(extract_id_from_url(primary))

    # Additional collections
    additional = metadata.get("ccm:collection_id", [])
    if isinstance(additional, list):
        for coll in additional:
            ids.append(extract_id_from_url(coll))

    seen: set[str] = set()
    unique = []
    for coll_id in ids:
        if coll_id and coll_id not in seen:
            seen.add(coll_id)
            unique.append(coll_id)
    return unique


async def set_collections(
    client: httpx.AsyncClient,
    auth_header: str,
    base_url: str,
    node_id: str,
    collection_ids: list[str],
) -> dict:
    """Reference the node in the given collections."""
    results = []

    for collection_id in collection_ids:
        # Metadata can carry anything a caller typed; only a node id may become
        # part of the authenticated URL.
        if not is_valid_node_id(collection_id):
            logger.warning(f"⚠️ Collection {collection_id!r} skipped: not a node id")
            results.append(
                {
                    "collectionId": collection_id,
                    "success": False,
                    "error": "Keine gültige Sammlungs-ID (erwartet wird eine Node-UUID)",
                }
            )
            continue
        try:
            url = f"{base_url}/rest/collection/v1/collections/-home-/{collection_id}/references/{node_id}"
            response = await client.put(
                url,
                headers={
                    "Authorization": auth_header,
                    "Accept": "application/json",
                },
            )
            success = response.status_code in (200, 201)
            entry: dict[str, Any] = {
                "collectionId": collection_id,
                "success": success,
            }
            if success:
                logger.info(f"📚 Referenced in collection {collection_id}")
            else:
                entry["error"] = repository_error(response)
                logger.warning(
                    f"⚠️ Collection {collection_id} failed: {response.status_code}"
                )
            results.append(entry)
        except Exception as e:
            logger.warning(
                f"⚠️ Collection {collection_id} failed: {type(e).__name__}: {e}"
            )
            # The text can name internal hosts: it stays in the log above.
            results.append(
                {
                    "collectionId": collection_id,
                    "success": False,
                    "error": f"Verknüpfung fehlgeschlagen ({type(e).__name__})",
                }
            )

    return {"results": results}


async def run_workflow_steps(
    client: httpx.AsyncClient,
    auth_header: str,
    base_url: str,
    node_id: str,
    steps: list[str],
    comment: Optional[str] = None,
    receiver: Optional[list[str]] = None,
) -> dict:
    """
    Walk the editorial review workflow one state at a time.

    edu-sharing records every PUT as its own entry in the node's workflow
    history, together with the acting user. Running the states one by one is
    therefore what makes the protocol readable later ("who confirmed the
    quality?") — a single jump to the final state would lose the trail.
    """
    workflow_url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/workflow"
    results: list[dict[str, Any]] = []

    for index, status in enumerate(steps):
        # A later state presumes the earlier ones: publishing after a failed
        # legal approval would release content nobody cleared. The remaining
        # steps are still listed, so the answer shows what did not happen.
        if results and not results[-1]["success"]:
            failed = results[-1]["status"]
            results.extend(
                {
                    "status": skipped,
                    "success": False,
                    "skipped": True,
                    "error": f"Nicht ausgeführt, weil {failed} fehlschlug",
                }
                for skipped in steps[index:]
            )
            break

        # The receiver group only makes sense for the handover state; the
        # later editorial states are recorded on the acting user (see the
        # 'receiver: []' of the editorial desk itself).
        step_receiver = receiver
        if step_receiver is None:
            step_receiver = (
                DEFAULT_WORKFLOW_RECEIVER if status == DEFAULT_WORKFLOW_STATUS else []
            )

        # Exactly the three fields WorkflowHistory accepts — anything else
        # (a 'logLevel' used to be sent here) makes edu-sharing answer 400
        # with UnrecognizedPropertyException and the state is never set.
        payload = {
            "receiver": [{"authorityName": name} for name in step_receiver],
            "comment": comment
            if comment is not None
            else "Upload via Metadata Agent API",
            "status": status,
        }

        try:
            response = await client.put(
                workflow_url,
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )
            success = response.status_code in (200, 201)
            entry: dict[str, Any] = {"status": status, "success": success}
            if success:
                logger.info(f"🔄 Workflow → {status}")
            else:
                entry["error"] = repository_error(response)
                logger.warning(f"⚠️ Workflow → {status} failed: {response.status_code}")
            results.append(entry)
        except Exception as e:
            logger.warning(f"⚠️ Workflow → {status} failed: {type(e).__name__}: {e}")
            # The text can name internal hosts: it stays in the log above.
            results.append(
                {
                    "status": status,
                    "success": False,
                    "error": f"Schritt fehlgeschlagen ({type(e).__name__})",
                }
            )

    return {"success": all(r["success"] for r in results), "steps": results}


async def fetch_workflow_history(
    client: httpx.AsyncClient, auth_header: str, base_url: str, node_id: str
) -> Optional[list[dict[str, Any]]]:
    """
    Read the workflow protocol of a node.

    Never raises: the workflow steps themselves already ran at this point, so
    a failed read must not turn a successful transition into an error.
    """
    url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/workflow"
    try:
        response = await client.get(
            url,
            headers={
                "Authorization": auth_header,
                "Accept": "application/json",
            },
        )
        if response.status_code != 200:
            logger.warning(
                f"⚠️ Workflow history of {node_id} unavailable: HTTP {response.status_code}"
            )
            return None
        data = response.json()
        if isinstance(data, list):
            return data
        return data.get("history") or data.get("workflow")
    except Exception as e:
        logger.warning(
            f"⚠️ Workflow history of {node_id} failed: {type(e).__name__}: {e}"
        )
        return None
