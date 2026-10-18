"""Repository nodes: creating, fetching, discarding, and the duplicate check before an upload."""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx

from ..metrics import OWN_SERVICE_HOOKS

from .repository_support import (
    first_value,
    identifying_url,
    node_created_at,
    node_modified_at,
    repository_error,
)

logger = logging.getLogger(__name__)


# Alfresco refuses these in cm:name, and trims dots and spaces at the edges.
_FORBIDDEN_IN_NAME = re.compile(r'[*"\\><?/:|]')

_MAX_NAME_LENGTH = 200


def derive_node_name(title: str) -> str:
    """
    Turn a title into a name Alfresco accepts.

    edu-sharing works `cm:name` out from `ccm:wwwurl` and answers
    `500 missing name` when there is none — it never falls back to the title.
    Measured against staging on 2026-08-12.
    """
    name = _FORBIDDEN_IN_NAME.sub("_", str(title))
    name = " ".join(name.split()).strip(" .")
    name = name[:_MAX_NAME_LENGTH].strip(" .")
    return name or "Unbenannt"


# Rolling back a half-finished upload runs *after* that upload has already spent
# its budget — typically after its own 45s timeout, inside a serverless
# invocation with a strict duration limit. A generous timeout here
# gets killed exactly in the slow-repository case the rollback exists for, and
# the orphaned node survives anyway. It is a single DELETE; keep it short.
# The lookup of a lost node (_find_lost_node) gets the same short budget. It is
# a total, not httpx's per-phase timeout: connect, write and read of one slow
# request would otherwise take three times as long.
DISCARD_TIMEOUT_SECONDS = 5.0

# A lost node may carry a creation time slightly before the agent's own clock
# said the request went out: the two clocks differ. NTP keeps them well within
# a second; anything more generous admits nodes of earlier uploads.
LOST_NODE_CLOCK_SKEW = timedelta(seconds=3)

# A lost node of ours was never written to after its create: we never had its
# id. Modified clearly later than created means another upload is filling it
# in (same URL, no workflow status yet). The create itself takes a moment.
LOST_NODE_UNTOUCHED = timedelta(seconds=5)


def _same_new_node(
    node: dict, not_before: datetime, wanted_url: Optional[str], wanted_title: Any
) -> bool:
    """
    Created after the request went out, with the same URL (or, without one, title).

    A node another upload works on - one with a workflow status, or one
    modified after its creation (or of unknown modification time) - is never
    taken for the lost one: discarding it would bin a concurrent upload of the
    same URL. Unsure means no candidate; the caller then reports the node.
    """
    created = node_created_at(node)
    if created is None or created < not_before:
        return False
    modified = node_modified_at(node)
    if modified is None or modified - created > LOST_NODE_UNTOUCHED:
        return False
    props = node.get("properties") or {}
    if first_value(props.get("ccm:wf_status")):
        return False
    if wanted_url:
        return first_value(props.get("ccm:wwwurl")) == wanted_url
    return bool(wanted_title) and first_value(props.get("cclom:title")) == wanted_title


class NodeMixin:
    """
    Node operations against the repository (part of RepositoryService).

    Needs _auth_header, set by RepositoryService.__init__.
    """

    _auth_header: str

    async def _discard_node(self, base_url: str, node_id: str) -> bool:
        """
        Move a node to the recycle bin. Never raises.

        Called while handling another error, so its own failure must not replace
        the original one — it is reported alongside instead.
        """
        url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}?recycle=true"
        try:
            async with (
                asyncio.timeout(DISCARD_TIMEOUT_SECONDS),
                httpx.AsyncClient(
                    timeout=httpx.Timeout(DISCARD_TIMEOUT_SECONDS),
                    event_hooks=OWN_SERVICE_HOOKS,
                ) as client,
            ):
                response = await client.delete(
                    url,
                    headers={
                        "Authorization": self._auth_header,
                        "Accept": "application/json",
                    },
                )
            if response.status_code in (200, 204):
                logger.info(f"🧹 Discarded incomplete node {node_id}")
                return True
            logger.warning(
                f"⚠️ Could not discard {node_id}: HTTP {response.status_code}"
            )
            return False
        except Exception as e:
            logger.warning(f"⚠️ Could not discard {node_id}: {type(e).__name__}: {e}")
            return False

    async def _find_lost_node(
        self,
        base_url: str,
        inbox_id: str,
        clean_metadata: dict,
        created_after: datetime,
    ) -> Optional[str]:
        """
        The node a create request made although its answer never arrived (audit D-01).

        Reads the newest children of the inbox straight from the repository (no
        search index, which lags behind a node created a second ago) and picks
        the one created after the request went out that carries the same URL -
        or, without a URL, the same title. Never raises; None when unsure.
        """
        url = (
            f"{base_url}/rest/node/v1/nodes/-home-/{inbox_id}/children"
            "?maxItems=10&skipCount=0&sortProperties=cm:created"
            "&sortAscending=false&propertyFilter=-all-"
        )
        wanted_url = identifying_url(clean_metadata)
        wanted_title = first_value(
            clean_metadata.get("cclom:title") or clean_metadata.get("schema:name")
        )
        # Clocks of agent and repository differ a little.
        not_before = created_after - LOST_NODE_CLOCK_SKEW
        try:
            async with (
                asyncio.timeout(DISCARD_TIMEOUT_SECONDS),
                httpx.AsyncClient(
                    timeout=httpx.Timeout(DISCARD_TIMEOUT_SECONDS),
                    event_hooks=OWN_SERVICE_HOOKS,
                ) as client,
            ):
                response = await client.get(
                    url,
                    headers={
                        "Authorization": self._auth_header,
                        "Accept": "application/json",
                    },
                )
            if response.status_code != 200:
                return None
            candidates = [
                node
                for node in response.json().get("nodes") or []
                if _same_new_node(node, not_before, wanted_url, wanted_title)
            ]
            # Two candidates: a concurrent upload or a retry of the same URL made
            # the other one. Unsure which is ours - report, do not discard.
            if len(candidates) == 1:
                return (candidates[0].get("ref") or {}).get("id")
        except Exception as e:
            logger.warning(f"⚠️ Looking for a lost node failed: {type(e).__name__}: {e}")
        return None

    async def _check_duplicate(
        self, client: httpx.AsyncClient, base_url: str, url: str
    ) -> dict:
        """Check if URL already exists in repository."""
        try:
            search_url = f"{base_url}/rest/search/v1/queries/-home-/mds_oeh/ngsearch"

            response = await client.post(
                search_url,
                headers={
                    "Authorization": self._auth_header,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                # Only 'criteria' — SearchParameters rejects unknown fields with
                # 400 (a 'facettes' key used to be sent here), which silently
                # turned every duplicate check into "no duplicate found".
                json={"criteria": [{"property": "ccm:wwwurl", "values": [url]}]},
            )

            if response.status_code != 200:
                logger.warning(f"⚠️ Duplicate check failed: {response.status_code}")
                return {"exists": False, "warning": f"HTTP {response.status_code}"}

            data = response.json()

            if data.get("nodes") and len(data["nodes"]) > 0:
                node = data["nodes"][0]
                props = node.get("properties", {})
                return {
                    "exists": True,
                    "nodeId": node["ref"]["id"],
                    "title": node.get("title") or props.get("cclom:title", [""])[0],
                    "description": props.get("cclom:general_description", [""])[0]
                    if props.get("cclom:general_description")
                    else None,
                }

            return {"exists": False}

        except Exception as e:
            logger.warning(f"⚠️ Duplicate check error: {e}")
            return {"exists": False, "warning": type(e).__name__}

    async def _create_node(
        self, client: httpx.AsyncClient, base_url: str, inbox_id: str, metadata: dict
    ) -> dict:
        """Create node with minimal essential fields."""
        create_url = f"{base_url}/rest/node/v1/nodes/-home-/{inbox_id}/children?type=ccm:io&renameIfExists=true&versionComment=MAIN_FILE_UPLOAD"

        # Essential fields for node creation, with fallbacks for different schemas
        # (e.g. organization uses schema:name instead of cclom:title)
        essential_fields_with_fallbacks = [
            ("cclom:title", ["schema:name"]),
            ("cclom:general_description", ["schema:description"]),
            ("cclom:general_keyword", []),
            ("ccm:wwwurl", ["schema:url"]),
            ("cclom:general_language", []),
        ]

        clean_metadata = {"ccm:linktype": ["USER_GENERATED"]}
        for field, fallbacks in essential_fields_with_fallbacks:
            value = metadata.get(field)
            # Try fallbacks if primary field is empty
            if value is None or value == "" or value == []:
                for fb in fallbacks:
                    value = metadata.get(fb)
                    if value is not None and value != "" and value != []:
                        break
            if value is not None and value != "" and value != []:
                # Normalize to array
                if isinstance(value, list):
                    clean_metadata[field] = value
                else:
                    clean_metadata[field] = [value]

        # cm:name is required, and edu-sharing only works one out when there is a
        # ccm:wwwurl to derive it from — otherwise it answers 500 'missing name'.
        # Supplied only in that gap: with a URL present the repository builds the
        # name itself ('example.org_b'), which is what every node uploaded so far
        # carries, and overriding that would rename them all.
        supplied_name = metadata.get("cm:name")
        if supplied_name:
            clean_metadata["cm:name"] = (
                supplied_name if isinstance(supplied_name, list) else [supplied_name]
            )
        elif not clean_metadata.get("ccm:wwwurl"):
            title = first_value(clean_metadata.get("cclom:title"))
            if title:
                clean_metadata["cm:name"] = [derive_node_name(title)]

        logger.info(f"📡 Creating node at: {create_url[:80]}...")
        response = await client.post(
            create_url,
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=clean_metadata,
        )

        if response.status_code not in (200, 201):
            return {
                "success": False,
                "error": f"Create node failed: {repository_error(response)}",
            }

        data = response.json()
        return {"success": True, "nodeId": data["node"]["ref"]["id"]}

    async def _fetch_full_node(
        self, client: httpx.AsyncClient, base_url: str, node_id: str
    ) -> Optional[dict[str, Any]]:
        """
        Read the complete node from the repository.

        Returns the raw edu-sharing node object (same shape as the 'node' entry of
        GET /node/v1/nodes/-home-/{id}/metadata), or None if the read fails.

        Never raises: the upload itself already succeeded at this point, so a
        failed read-back must not turn a successful upload into an error.
        """
        node, _ = await self._read_node(client, base_url, node_id)
        return node

    async def _read_node(
        self, client: httpx.AsyncClient, base_url: str, node_id: str
    ) -> tuple[Optional[dict[str, Any]], bool]:
        """
        (node, False), or (None, missing) when the read fails. Never raises.

        `missing` is True only when the repository itself says the node is not
        there for this account (403, 404); an outage, a timeout or a broken
        answer says nothing about the node and gives False.
        """
        url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/metadata?propertyFilter=-all-"
        try:
            response = await client.get(
                url,
                headers={
                    "Authorization": self._auth_header,
                    "Accept": "application/json",
                },
            )
            if response.status_code != 200:
                logger.warning(
                    f"⚠️ Read of node {node_id} failed: HTTP {response.status_code}"
                )
                return None, response.status_code in (403, 404)
            node = response.json().get("node")
            return (node, False) if isinstance(node, dict) else (None, False)
        except Exception as e:
            logger.warning(f"⚠️ Read of node {node_id} failed: {type(e).__name__}: {e}")
            return None, False
