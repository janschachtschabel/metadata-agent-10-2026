"""
Repository ids and workflow states: what the request models check and the
services use (audit M-05 - models no longer import from services).
"""

import re
from typing import Any
from urllib.parse import unquote

# edu-sharing node ids are Alfresco UUIDs.
_NODE_ID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def is_valid_node_id(value: Any) -> bool:
    """
    Check whether a value is a usable edu-sharing node id.

    Every node id the API accepts ends up interpolated into a repository URL
    that is called with the service account's credentials. A value containing
    '/', '?' or '#' would steer that authenticated request at a different
    endpoint, so callers must reject anything that is not the plain UUID shape.
    Collection ids are node ids too.
    """
    return isinstance(value, str) and _NODE_ID_PATTERN.fullmatch(value) is not None


# Review workflow status used when a node is handed over for human checking.
# This is the state the upload flow stops at unless further steps are requested.
DEFAULT_WORKFLOW_STATUS = "200_tocheck"

# Receiver group for DEFAULT_WORKFLOW_STATUS — the WLO upload management queue.
DEFAULT_WORKFLOW_RECEIVER = ["GROUP_ORG_WLO-Uploadmanager"]

# Editorial state reached once a node has been checked and released. Where
# DEFAULT_WORKFLOW_STATUS hands the node over for checking, this one records the
# outcome, so the two are separate choices rather than steps of one switch.
QUALITY_WORKFLOW_STATUS = "140_ELEMENT_LEGALLY_APPROVED"

# Workflow states of the WLO editorial process, read from the repository config
# (/rest/config/v1/values → workflow.workflows) plus the two states that occur in
# live data but are not listed there ('200_tocheck', '125_METADATA_QUALITY_FOR_BUFFET').
# Kept as a documented allow-list so a typo fails fast instead of writing an
# unknown state into the repository.
KNOWN_WORKFLOW_STATUS = {
    "100_unchecked",
    "110_METADATA_RECORD_REQUESTED",
    "120_METADATA_QUALITY_CONFIRMED",
    "125_METADATA_QUALITY_FOR_BUFFET",
    "130_ELEMENT_REJECTED",
    "140_ELEMENT_LEGALLY_APPROVED",
    "150_PUBLISH_IN_SEARCH",
    "160_REMOVE_FROM_SEARCH",
    "200_tocheck",
    "TASK_CREATE_TREE",
    "TASK_CHECK_COLLECTION_PROPOSAL",
    "TASK_CHECK_QUALITY",
}


def extract_id_from_url(value: Any) -> str:
    """
    Extract a collection node ID from a plain ID or a repository URL.

    Handles the shapes users actually paste from the WLO frontend:
    - '3039bdb2-…'                                    → as-is
    - '…/components/collections/3039bdb2-…'           → last path segment
    - '…/components/collections?id=3039bdb2-…&x=1'    → 'id' query parameter
    """
    if not isinstance(value, str):
        return str(value) if value else ""

    candidate = value.strip()
    if not candidate:
        return ""

    # Collection links carry the node in the 'id' query parameter
    if "?" in candidate:
        base, _, query = candidate.partition("?")
        for part in query.split("&"):
            key, _, val = part.partition("=")
            if key == "id" and val:
                return unquote(val).split("/")[-1]
        candidate = base

    candidate = candidate.split("#")[0].rstrip("/")
    return unquote(candidate).split("/")[-1]
