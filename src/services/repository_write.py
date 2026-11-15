"""
One write to the repository: what was asked for (NodeWrite), where it goes
(WriteTarget), how far it got (WriteProgress) and what its steps answered
(StepResults). The flow itself is RepositoryService.write_node.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from ..models.repository_rules import DEFAULT_WORKFLOW_STATUS
from ..utils.schema_loader import resolve_schema_file_or_uri

# A preview image: awaited before the workflow runs, raises when there is none.
PreviewSource = Callable[[], Awaitable[tuple[bytes, str]]]


@dataclass
class NodeWrite:
    """One write of metadata to the repository; /upload and /nodes describe theirs."""

    metadata: dict[str, Any]
    context: str = "default"
    version: str = "latest"
    # Node to rewrite (inside NODE_EDIT_WINDOW); None creates one in the inbox.
    node_id: Optional[str] = None
    check_duplicates: bool = False
    # Read the finished node (or the duplicate) back into 'node_full'.
    read_back: bool = False
    write_extended_data: bool = True
    extended_text: Optional[str] = None
    collection_ids: Optional[list[str]] = None
    # Workflow states run in order; empty runs none.
    workflow_steps: list[str] = field(default_factory=list)
    workflow_comment: Optional[str] = None
    workflow_receiver: Optional[list[str]] = None
    preview_source: Optional[PreviewSource] = None


def upload_workflow_steps(
    start_workflow: bool, workflow_steps: Optional[list[str]]
) -> list[str]:
    """
    The states /upload runs: the named ones, without any the single handover
    state; none when the workflow is switched off.
    """
    return (workflow_steps or [DEFAULT_WORKFLOW_STATUS]) if start_workflow else []


def with_resolved_schema(job: NodeWrite) -> NodeWrite:
    """
    The job with `metadataset` as a schema file name.

    /validate, /generate and /extract-field take the content type's URI there
    as well; the write path read it as a file name, found no schema and wrote
    the core fields only (audit 13.11., API-13). A URI that names no content
    type is left for the checks that follow.
    """
    metadataset = job.metadata.get("metadataset")
    if not isinstance(metadataset, str) or not metadataset.startswith("http"):
        return job
    try:
        schema_file = resolve_schema_file_or_uri(metadataset, job.context, job.version)
    except ValueError:
        return job
    return replace(job, metadata={**job.metadata, "metadataset": schema_file})


@dataclass
class WriteTarget:
    """What the write may put where: the cleaned metadata and the schema's fields."""

    clean_metadata: dict[str, Any]
    schema_file: Optional[str]
    repo_field_ids: Any
    inbox_id: str
    # Properties of a handed-over node, as the edit-window check read them.
    node_properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class WriteProgress:
    """What a write has done so far, for the cleanup when it fails or is cut off."""

    # Only ever a node this call created: a node handed over by the caller
    # holds their content and is not ours to bin.
    created_node_id: Optional[str] = None
    # Set while a create request is out: its node may exist without an id.
    creating_since: Optional[datetime] = None
    # The create answer got lost and no new node turned up in the inbox.
    maybe_created: bool = False
    # A write request to the node has gone out (aspects or metadata).
    node_touched: bool = False
    # The collections asked for and the answers so far; None until the first
    # request goes out. From then on a reference may point at the node: it is
    # kept like one in the workflow (audit 14.11., R-01).
    collection_ids: list[str] = field(default_factory=list)
    collection_results: Optional[list[dict[str, Any]]] = None
    # From here the node is in the editorial process: never discarded.
    workflow_started: bool = False
    # Every write step ran: the node is complete and kept, whatever follows
    # (building the answer, the read-back, a client that went away).
    finished: bool = False

    @property
    def keeps_node(self) -> bool:
        return (
            self.workflow_started
            or self.finished
            or self.collection_results is not None
        )

    def collections_at_cut_off(self) -> list[dict[str, Any]]:
        """Every collection asked for: answered, sent without answer, or not tried."""
        answered = list(self.collection_results or [])
        open_ids = self.collection_ids[len(answered) :]
        return answered + [
            {
                "collectionId": collection_id,
                "success": False,
                # Only the first open one was sent: the requests run in order.
                "error": "Verknüpfung nicht bestätigt — die Anfrage wurde unterbrochen"
                if position == 0
                else "Verknüpfung nicht versucht — vorher unterbrochen",
            }
            for position, collection_id in enumerate(open_ids)
        ]


@dataclass
class StepResults:
    collections: Optional[dict[str, Any]] = None
    extended: Optional[dict[str, Any]] = None
    preview: Optional[dict[str, Any]] = None
    workflow: Optional[dict[str, Any]] = None
