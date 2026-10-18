"""Repository service for uploading metadata to WLO edu-sharing repository."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote

import httpx

from ..metrics import OWN_SERVICE_HOOKS, THIRD_PARTY_HOOKS
from ..utils.schema_loader import get_repo_fields
from .field_normalizer import find_object_shape_violations
from .repository_curation import (
    DEFAULT_WORKFLOW_STATUS,
    extract_collection_ids,
    run_workflow_steps,
    set_collections,
)

# Re-exported: callers and tests import these from repository_service, as before 3.0.0.
from .repository_metadata import MetadataWriteMixin
from .repository_nodes import NodeMixin
from .repository_preview import MAX_PREVIEW_BYTES as MAX_PREVIEW_BYTES
from .repository_preview import PreviewError as PreviewError
from .repository_preview import resolve_preview as resolve_preview
from .repository_result import METADATA_INVALID, WRITE_FAILED
from .repository_review import ReviewMixin
from .repository_support import get_repository_config as get_repository_config

# Shared helpers the flows below use.
from .repository_support import (
    first_value,
    text_value,
    repository_error,
    no_writable_fields_result,
    identifying_url,
    nothing_written,
    partial_problems,
    duplicate_skipped,
    node_created_at,
)
from .repository_support import build_auth_header as build_auth_header

# Public names this module defined before 3.0.0 (tests/test_import_compatibility.py).
from .repository_metadata import EXTENDED_TYPE_TO_NEW_LRT as EXTENDED_TYPE_TO_NEW_LRT
from .repository_nodes import DISCARD_TIMEOUT_SECONDS as DISCARD_TIMEOUT_SECONDS
from .repository_nodes import derive_node_name as derive_node_name
from .repository_preview import MAX_PREVIEW_REDIRECTS as MAX_PREVIEW_REDIRECTS
from .repository_preview import PREVIEW_USER_AGENT as PREVIEW_USER_AGENT

logger = logging.getLogger(__name__)

# The name before 3.0.0, kept so existing imports keep working.
_get_repository_config = get_repository_config


# How long after its creation a node may still be rewritten through this service.
# Past that point the content has been in the editorial queue long enough for a
# human to have worked on it, and an automated write would replace that work
# without anyone being told. The window is deliberately short: it covers the
# session that produced the node, not later revisions.
NODE_EDIT_WINDOW = timedelta(hours=2)

# A preview image: awaited before the workflow runs, raises when there is none.
PreviewSource = Callable[[], Awaitable[tuple[bytes, str]]]

# Seconds of the write deadline the preview may not use: the workflow PUTs that
# follow it still have to fit. A capture may take up to 90 s, the deadline
# defaults to 100 s - without the reserve a slow page ends the write at the
# deadline and the finished node is discarded. The preview is not worth that.
PREVIEW_RESERVE_SECONDS = 15.0
# Without workflow steps nothing follows the preview inside the deadline; this
# margin keeps its own limit ahead of the deadline, so the preview is what
# gets cut, not the write.
PREVIEW_MARGIN_SECONDS = 1.0

# The read-back after a finished write runs outside the deadline with this limit
# of its own: the node is complete, a slow read must not turn it into a 504.
# Deadline (100 s) + read-back + preStop (5 s) stays below the 120 s of ingress
# and grace period; the lookup and discard of a cut-off write never run on
# the same request.
READ_BACK_SECONDS = 10.0

# After these the create request may have reached the repository (audit D-01).
_ANSWER_MAYBE_LOST = (
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)


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


@dataclass
class _Target:
    """What the write may put where: the cleaned metadata and the schema's fields."""

    clean_metadata: dict[str, Any]
    schema_file: Optional[str]
    repo_field_ids: Any
    inbox_id: str
    # Properties of a handed-over node, as the edit-window check read them.
    node_properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Progress:
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
    # From here the node is in the editorial process: never discarded.
    workflow_started: bool = False
    # Every write step ran: the node is complete and kept, whatever follows
    # (building the answer, the read-back, a client that went away).
    finished: bool = False

    @property
    def keeps_node(self) -> bool:
        return self.workflow_started or self.finished


@dataclass
class _StepResults:
    collections: Optional[dict[str, Any]] = None
    extended: Optional[dict[str, Any]] = None
    preview: Optional[dict[str, Any]] = None
    workflow: Optional[dict[str, Any]] = None


class RepositoryService(NodeMixin, MetadataWriteMixin, ReviewMixin):
    """
    Service for uploading metadata to WLO edu-sharing repository.

    Write steps (_write_steps / _after_metadata):
    1. Check for duplicates (by ccm:wwwurl), unless a node was handed over
    2. Create the node with minimal data, or check the handed-over node's
       editing window
    3. Add the aspects the special fields need, then set the metadata
    4. Reference the node in collections (optional)
    5. Write the extended fields (optional)
    6. Store the preview image (optional; within what the deadline leaves)
    7. Run the review workflow steps (from here the node is never discarded)
    8. Read the finished node back (optional; outside the deadline)
    """

    def __init__(self, username: str, password: str):
        """
        Initialize repository service with credentials.

        Args:
            username: WLO guest upload username
            password: WLO guest upload password
        """
        self.username = username
        self.password = password
        self._auth_header = self._create_auth_header()

    def _create_auth_header(self) -> str:
        """Create Basic Auth header (credentials are guaranteed by the factory)."""
        return build_auth_header(self.username, self.password) or ""

    async def create_node(
        self,
        metadata: dict[str, Any],
        check_duplicates: bool = False,
        deadline: Optional[float] = None,
    ) -> dict[str, Any]:
        """
        Create a node and stop there, so the caller has an id to work with.

        The first half of `upload_metadata`, on its own: the same five fields the
        upload path puts on a new node (title, description, keywords, url,
        language) plus `ccm:linktype`. Everything beyond that needs the schema
        filter, which is what handing the id back to `/upload` is for.

        A title is required. A node without one is unfindable in the inbox, and
        the point of this call is to get something a human can refer to.

        When the answer to the create request gets lost, the node is looked up
        in the inbox like on /upload (audit D-01) - and handed to the caller,
        who asked for it. At the deadline (loop time) the answer has error_code
        upload_timeout and names a node found meanwhile.
        """
        config = get_repository_config()
        base_url = config["base_url"]

        clean_metadata = self._extract_metadata_fields(metadata)
        title = clean_metadata.get("cclom:title") or clean_metadata.get("schema:name")
        if not title:
            return {
                "success": False,
                "error_code": "title_missing",
                "error": "cclom:title fehlt — ein Node ohne Titel ist im Eingangsordner nicht auffindbar.",
            }

        target = _Target(clean_metadata, None, None, config["inbox_id"])
        progress = _Progress()
        try:
            timeout = httpx.Timeout(45.0, connect=10.0)
            async with httpx.AsyncClient(
                timeout=timeout, event_hooks=OWN_SERVICE_HOOKS
            ) as client:
                async with asyncio.timeout_at(deadline):
                    return await self._create_only(
                        client, base_url, target, progress, check_duplicates
                    )
        except TimeoutError:
            return await self._create_cut_off(base_url, target, progress)
        except _ANSWER_MAYBE_LOST:
            if progress.created_node_id:
                return self._created_result(
                    base_url, progress.created_node_id, clean_metadata
                )
            return {
                "success": False,
                "error": "Die Antwort des Repositorys ging verloren — möglicherweise "
                "wurde trotzdem ein Node angelegt; bitte den Eingangsordner prüfen",
            }
        except Exception as e:
            # The text can name internal hosts: log it, answer with the type.
            logger.error(f"❌ Node creation failed: {type(e).__name__}: {e}")
            return {
                "success": False,
                "error": f"Node konnte nicht angelegt werden ({type(e).__name__})",
            }

    async def _create_only(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        target: "_Target",
        progress: "_Progress",
        check_duplicates: bool,
    ) -> dict[str, Any]:
        """The requests of POST /node: duplicate check (optional), create."""
        clean_metadata = target.clean_metadata
        duplicate_warning = None
        if check_duplicates:
            url = identifying_url(clean_metadata)
            if url:
                duplicate = await self._check_duplicate(client, base_url, url)
                if duplicate.get("exists"):
                    return await self._duplicate_result(
                        client, base_url, url, duplicate, read_back=False
                    )
                duplicate_warning = duplicate.get("warning")

        result = await self._create_tracked(client, base_url, target, progress)
        if not result.get("success"):
            return result
        created = self._created_result(base_url, result["nodeId"], clean_metadata)
        if duplicate_warning:
            created["error"] = duplicate_skipped(duplicate_warning)
        return created

    def _created_result(
        self, base_url: str, node_id: str, clean_metadata: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "success": True,
            "node": {
                "nodeId": node_id,
                "title": text_value(clean_metadata.get("cclom:title")),
                "wwwurl": identifying_url(clean_metadata),
                "repositoryUrl": f"{base_url}/components/render/{node_id}",
            },
        }

    async def _create_cut_off(
        self, base_url: str, target: "_Target", progress: "_Progress"
    ) -> dict[str, Any]:
        """The answer for a POST /node its deadline ended; a node found is named."""
        node_id = progress.created_node_id
        if node_id is None and progress.creating_since is not None:
            node_id = await self._find_lost_node(
                base_url,
                target.inbox_id,
                target.clean_metadata,
                progress.creating_since,
            )
        result: dict[str, Any] = {"success": False, "error_code": "upload_timeout"}
        if node_id:
            result["node"] = self._created_result(
                base_url, node_id, target.clean_metadata
            )["node"]
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

    async def upload_metadata(
        self,
        metadata: dict[str, Any],
        repository: str = "staging",
        check_duplicates: bool = True,
        start_workflow: bool = True,
        context: str = "default",
        version: str = "latest",
        write_extended_data: bool = True,
        extended_text: Optional[str] = None,
        return_full_node: bool = False,
        collection_ids: Optional[list[str]] = None,
        workflow_steps: Optional[list[str]] = None,
        workflow_comment: Optional[str] = None,
        workflow_receiver: Optional[list[str]] = None,
        node_id: Optional[str] = None,
        preview_source: Optional[PreviewSource] = None,
        deadline: Optional[float] = None,
    ) -> dict[str, Any]:
        """
        Upload metadata to WLO repository (/upload).

        `preview_source` returns the preview image (bytes, mimetype), e.g. a
        screenshot captured meanwhile. It is awaited and stored before the
        workflow runs, so the node reaches the editorial queue with its picture;
        a failing source is reported in `preview` and does not stop the upload.
        With a deadline it is awaited only until PREVIEW_RESERVE_SECONDS before
        it, then reported as failed.

        Args:
            metadata: Metadata dict from /generate endpoint
            repository: Ignored (kept for backward compatibility)
            check_duplicates: Check for duplicates by ccm:wwwurl
            start_workflow: Run the review workflow after upload
            write_extended_data: Write ccm:oeh_extendedType/Data/Text fields
            extended_text: Raw source text before extraction
            return_full_node: Read the node back and include it as 'node_full'
            collection_ids: Collection IDs (or collection URLs) the new node is
                referenced in, additionally to any collection found in metadata
            workflow_steps: Workflow states to run in order. Defaults to the
                single handover state DEFAULT_WORKFLOW_STATUS.
            workflow_comment: Comment written with every workflow step
            workflow_receiver: Authority names notified by every workflow step
            node_id: Fill in this node (inside NODE_EDIT_WINDOW) instead of
                creating one
            deadline: Event loop time at which the write stops; the answer
                then has error_code upload_timeout and says what happened to
                the node

        Returns:
            Upload result with nodeId, success status, etc.
        """
        steps = (workflow_steps or [DEFAULT_WORKFLOW_STATUS]) if start_workflow else []
        return await self._write_node(
            NodeWrite(
                metadata=metadata,
                context=context,
                version=version,
                node_id=node_id or None,
                check_duplicates=check_duplicates,
                read_back=return_full_node,
                write_extended_data=write_extended_data,
                extended_text=extended_text,
                collection_ids=collection_ids,
                workflow_steps=steps,
                workflow_comment=workflow_comment,
                workflow_receiver=workflow_receiver,
                preview_source=preview_source,
            ),
            deadline,
        )

    async def save_node(
        self,
        metadata: dict[str, Any],
        node_id: Optional[str] = None,
        context: str = "default",
        version: str = "latest",
        write_extended_data: bool = True,
        extended_text: Optional[str] = None,
        collection_ids: Optional[list[str]] = None,
        workflow_steps: Optional[list[str]] = None,
        preview: Optional[str] = None,
        deadline: Optional[float] = None,
    ) -> dict[str, Any]:
        """
        Write metadata to the repository — creating a node or updating one (/nodes).

        Without `node_id` a node is created in the inbox. With one, that node is
        rewritten, provided it is still within NODE_EDIT_WINDOW. Fields the
        caller leaves out stay as they are: `normalize_for_repo` drops empty
        values, so an update carries only what it actually names.

        Args:
            metadata: Metadata dict from the /generate endpoint
            node_id: Node to update. Absent means: create a new one.
            context: Schema context deciding which fields may be written
            version: Schema version deciding which fields may be written
            write_extended_data: Write ccm:oeh_extendedType/Data/Text
            extended_text: Raw source text before extraction
            collection_ids: Collections the node is referenced in, additionally
                to any collection named in the metadata
            workflow_steps: Workflow states to run in order after writing.
                None or empty runs none.
            preview: Preview image for the node, as an http(s) URL or as inline
                image data (data URL or bare base64)

        Returns:
            Result dict with the node, the write counts and the full node.
        """
        return await self._write_node(
            NodeWrite(
                metadata=metadata,
                context=context,
                version=version,
                node_id=node_id or None,
                read_back=True,
                write_extended_data=write_extended_data,
                extended_text=extended_text,
                collection_ids=collection_ids,
                workflow_steps=list(workflow_steps or []),
                preview_source=self._fetched_preview(preview) if preview else None,
            ),
            deadline,
        )

    async def _write_node(
        self, job: NodeWrite, deadline: Optional[float] = None
    ) -> dict[str, Any]:
        """
        The one write path of /upload and /nodes.

        Refuses what may not be written before the first request, then: node,
        aspects, metadata, collections, extended fields, preview, workflow,
        read-back. A node this call created is discarded when a later step fails
        or the request is cut off; at the deadline (loop time) the answer says
        what happened to it (error_code upload_timeout, audit D-04).
        """
        config = get_repository_config()
        base_url = config["base_url"]
        clean_metadata = self._extract_metadata_fields(job.metadata)
        schema_file = job.metadata.get("metadataset") or None

        # Refuse before anything is written. A value whose shape contradicts the
        # schema cannot be silently corrected here: the caller reviewed what they
        # sent, and the repository must hold that, not a version of it nobody saw.
        # Checked ahead of the first request so no half-written node is left over.
        if schema_file:
            violations = find_object_shape_violations(
                clean_metadata, job.context, job.version, schema_file
            )
            if violations:
                return {
                    "success": False,
                    "error_code": METADATA_INVALID,
                    "error": "Metadaten wurden nicht geschrieben: "
                    + "; ".join(v["message"] for v in violations),
                    "violations": violations,
                }

        repo_field_ids = get_repo_fields(job.context, job.version, schema_file)
        logger.info(f"📋 Repo fields from schema: {len(repo_field_ids)} fields")
        if schema_file:
            logger.info(f"   Schemas: core.json + {schema_file}")
        if not repo_field_ids:
            return no_writable_fields_result(job.context, job.version, schema_file)

        target = _Target(
            clean_metadata, schema_file, repo_field_ids, config["inbox_id"]
        )
        progress = _Progress()
        try:
            # Longer timeout for sequential edu-sharing calls on hosted runtimes
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(45.0, connect=10.0),
                event_hooks=OWN_SERVICE_HOOKS,
            ) as client:
                # The deadline covers the write requests, not the cleanup after
                # it (below) nor the read-back of a finished node (own limit).
                async with asyncio.timeout_at(deadline):
                    result = await self._write_steps(
                        client, base_url, job, target, progress, deadline
                    )
                if job.read_back and result.get("success"):
                    result["node_full"] = await self._read_back(
                        client, base_url, result["node"]["nodeId"]
                    )
                return result
        except TimeoutError:
            # Shielded like the cleanup below: a cancel arriving now (client
            # gone, shutdown) must not abort the DELETE and leave an orphan.
            return await asyncio.shield(
                self._cut_off_result(base_url, job, target, progress)
            )
        except asyncio.CancelledError:
            # The client went away. Shielded: the cleanup is one short DELETE
            # and must not be cut off as well.
            await asyncio.shield(self._clean_up_cut_off(base_url, target, progress))
            raise
        # The exception texts can name internal hosts: they go to the log, the
        # answer names the type (audit S-06).
        except httpx.TimeoutException as e:
            logger.error(f"❌ Repository write timed out: {type(e).__name__}: {e}")
            return await self._write_failed(
                base_url,
                job,
                progress,
                f"Timeout bei der Verbindung zum Repository ({type(e).__name__})",
            )
        except httpx.ConnectError as e:
            logger.error(f"❌ Repository connection failed: {e}")
            return await self._write_failed(
                base_url,
                job,
                progress,
                f"Verbindung zum Repository fehlgeschlagen ({type(e).__name__})",
            )
        except Exception as e:
            logger.error(f"❌ Repository write failed: {type(e).__name__}: {e}")
            return await self._write_failed(
                base_url,
                job,
                progress,
                f"Schreiben ins Repository fehlgeschlagen ({type(e).__name__})",
            )

    async def _read_back(
        self, client: httpx.AsyncClient, base_url: str, node_id: str
    ) -> Optional[dict[str, Any]]:
        """The finished node, or None when it cannot be read within READ_BACK_SECONDS."""
        try:
            async with asyncio.timeout(READ_BACK_SECONDS):
                return await self._fetch_full_node(client, base_url, node_id)
        except TimeoutError:
            logger.warning(f"⚠️ Read-back of node {node_id} timed out")
            return None

    async def _write_failed(
        self, base_url: str, job: NodeWrite, progress: "_Progress", error: str
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

    async def _write_steps(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        job: NodeWrite,
        target: "_Target",
        progress: "_Progress",
        deadline: Optional[float] = None,
    ) -> dict[str, Any]:
        """The requests of one write, in the order the editorial process needs them."""
        clean_metadata = target.clean_metadata

        # 1. Check for duplicates. Skipped for a node that was handed over: the
        # check searches by ccm:wwwurl, and the node being filled in already
        # carries that URL — it would find itself and refuse the upload.
        duplicate_warning = None
        if job.check_duplicates and not job.node_id:
            url = identifying_url(clean_metadata)
            if url:
                duplicate = await self._check_duplicate(client, base_url, url)
                if duplicate.get("exists"):
                    return await self._duplicate_result(
                        client, base_url, url, duplicate, job.read_back
                    )
                duplicate_warning = duplicate.get("warning")

        # 2. Create the node, or check that the given one may still be written:
        # after the window it has reached the editors, and this call would
        # replace their work.
        if job.node_id:
            refusal, existing = await self._edit_check(client, base_url, job.node_id)
            if refusal is not None:
                return refusal
            target.node_properties = (existing or {}).get("properties") or {}
            node_id = job.node_id
            logger.info(f"📌 Writing existing node: {node_id}")
        else:
            node_result = await self._create_tracked(client, base_url, target, progress)
            if not node_result.get("success"):
                return {**node_result, "error_code": WRITE_FAILED}
            node_id = node_result["nodeId"]

        # 3. The aspects the special fields need, then the metadata the schema
        # allows. Collections and workflow would put a node holding only its
        # title in front of editors — stop here instead.
        progress.node_touched = True
        aspect_problem = await self._ensure_aspects(
            client, base_url, node_id, clean_metadata
        )
        metadata_result = await self._set_metadata(
            client, base_url, node_id, clean_metadata, target.repo_field_ids
        )
        if nothing_written(metadata_result):
            return await self._metadata_rejected(
                base_url, progress.created_node_id, metadata_result
            )

        steps = await self._after_metadata(
            client, base_url, node_id, job, target, progress, deadline
        )
        progress.finished = True
        result = self._written_result(
            base_url, node_id, target, progress, metadata_result, steps
        )
        problems = partial_problems(
            metadata_result,
            aspect_problem,
            steps.extended,
            steps.collections,
            steps.workflow,
            steps.preview,
        )
        if duplicate_warning:
            problems.insert(0, duplicate_skipped(duplicate_warning))
        if problems:
            result["error"] = "; ".join(problems)
        # 8. The read-back (job.read_back) follows in _write_node, outside the
        # deadline: the node is complete by now.
        return result

    async def _after_metadata(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str,
        job: NodeWrite,
        target: "_Target",
        progress: "_Progress",
        deadline: Optional[float] = None,
    ) -> "_StepResults":
        """Collections, extended fields, preview, workflow — each reported, none fatal."""
        steps = _StepResults()

        # 4. Reference the node in collections (from metadata and/or request)
        all_collection_ids = extract_collection_ids(
            target.clean_metadata, job.collection_ids
        )
        if all_collection_ids:
            steps.collections = await set_collections(
                client, self._auth_header, base_url, node_id, all_collection_ids
            )

        # 5. Write extended data fields (bypasses repo_field filter)
        if job.write_extended_data:
            steps.extended = await self._write_extended_fields(
                client,
                base_url,
                node_id,
                job.metadata,
                job.context,
                job.version,
                job.extended_text,
                # A partial update must not put the coarse derived LRT over
                # the precise one the node already carries.
                keep_lrt=bool(first_value(target.node_properties.get("ccm:oeh_lrt"))),
            )

        # 6. Preview before the workflow, so whoever picks the node out of the
        # queue sees it
        if job.preview_source:
            reserve = (
                PREVIEW_RESERVE_SECONDS
                if job.workflow_steps
                else PREVIEW_MARGIN_SECONDS
            )
            steps.preview = await self._bounded_preview(
                client, base_url, node_id, job.preview_source, deadline, reserve
            )

        # 7. The workflow states, each its own history entry
        if job.workflow_steps:
            progress.workflow_started = True
            steps.workflow = await run_workflow_steps(
                client,
                self._auth_header,
                base_url,
                node_id,
                job.workflow_steps,
                job.workflow_comment,
                job.workflow_receiver,
            )
        return steps

    async def _create_tracked(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        target: "_Target",
        progress: "_Progress",
    ) -> dict[str, Any]:
        """
        Create the node and remember it, so a failure afterwards can undo it.

        When the answer to the create request gets lost, the repository may have
        created the node anyway; it is looked up in the inbox (audit D-01).
        """
        progress.creating_since = datetime.now(timezone.utc)
        try:
            node_result = await self._create_node(
                client, base_url, target.inbox_id, target.clean_metadata
            )
        except _ANSWER_MAYBE_LOST:
            progress.created_node_id = await self._find_lost_node(
                base_url,
                target.inbox_id,
                target.clean_metadata,
                progress.creating_since,
            )
            progress.maybe_created = progress.created_node_id is None
            raise
        progress.creating_since = None
        if node_result.get("success"):
            progress.created_node_id = node_result["nodeId"]
            logger.info(f"✅ Created node: {node_result['nodeId']}")
        return node_result

    async def _clean_up_cut_off(
        self, base_url: str, target: "_Target", progress: "_Progress"
    ) -> Optional[dict[str, Any]]:
        """
        Undo what a cut-off write created, and say what happened to the node.

        Returns the node as the answer names it: discarded, handed back for
        cleanup, or kept because it already went into the workflow (the
        editors may have been notified). None when no node was touched.
        """
        node_id = progress.created_node_id
        if node_id is None and progress.creating_since is not None:
            node_id = await self._find_lost_node(
                base_url,
                target.inbox_id,
                target.clean_metadata,
                progress.creating_since,
            )
        if node_id is None:
            return None
        info = {
            "nodeId": node_id,
            "repositoryUrl": f"{base_url}/components/render/{node_id}",
        }
        if progress.keeps_node:
            where = "und im Workflow" if progress.workflow_started else "vollständig"
            return {
                "node": info,
                "note": f"Node {node_id} ist geschrieben {where}; "
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
        self, base_url: str, job: NodeWrite, target: "_Target", progress: "_Progress"
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
        elif progress.creating_since is not None:
            note = (
                "möglicherweise wurde trotzdem ein Node angelegt; bitte den "
                "Eingangsordner prüfen"
            )
        else:
            note = "es wurde kein Node angelegt"
        result["error"] = f"Zeitlimit beim Schreiben ins Repository erreicht — {note}"
        return result

    async def _duplicate_result(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        url: str,
        duplicate: dict[str, Any],
        read_back: bool,
    ) -> dict[str, Any]:
        """The refusal for a URL the repository already holds, with that node."""
        existing = duplicate.get("nodeId")
        result: dict[str, Any] = {
            "success": False,
            "duplicate": True,
            "node": {
                "nodeId": existing,
                "title": duplicate.get("title"),
                "description": duplicate.get("description"),
                "wwwurl": url,
                "repositoryUrl": f"{base_url}/components/render/{existing}",
            },
            "error": f'URL existiert bereits: "{duplicate.get("title")}"',
        }
        if read_back and existing:
            result["node_full"] = await self._fetch_full_node(
                client, base_url, existing
            )
        return result

    def _written_result(
        self,
        base_url: str,
        node_id: str,
        target: "_Target",
        progress: "_Progress",
        metadata_result: dict[str, Any],
        steps: "_StepResults",
    ) -> dict[str, Any]:
        """The answer for a write that went through, before problems and read-back."""
        clean_metadata = target.clean_metadata
        # With fallbacks for the organization schema
        title = text_value(
            clean_metadata.get("cclom:title") or clean_metadata.get("schema:name")
        )
        description = text_value(
            clean_metadata.get("cclom:general_description")
            or clean_metadata.get("schema:description")
        )
        result: dict[str, Any] = {
            "success": True,
            # Which schema decided what may be written. Without it a caller
            # cannot tell a complete upload from one that quietly fell back to
            # core.json and dropped the type-specific fields — both answer 200.
            "schema_used": target.schema_file,
            "repo_fields_available": len(target.repo_field_ids),
            # False when the caller handed the node over. Both answer 200 with
            # the same nodeId, so nothing else tells them apart.
            "node_created": progress.created_node_id is not None,
            "node": {
                "nodeId": node_id,
                "title": title,
                "description": description[:200] + "..."
                if description and len(description) > 200
                else description,
                "wwwurl": identifying_url(clean_metadata),
                "repositoryUrl": f"{base_url}/components/render/{node_id}",
            },
            "fields_written": metadata_result.get("fields_written", 0),
            "fields_skipped": metadata_result.get("fields_skipped", 0),
        }
        if steps.collections is not None:
            result["collections"] = steps.collections["results"]
        if steps.workflow is not None:
            result["workflow"] = steps.workflow["steps"]
        if steps.preview is not None:
            result["preview"] = steps.preview
        field_errors = metadata_result.get("field_errors", [])
        if field_errors:
            result["field_errors"] = field_errors
        return result

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

    async def preview_refusal(self, node_id: str) -> Optional[dict[str, Any]]:
        """
        The refusal upload_preview would answer for this node, or None.

        For /screenshot, which checks before it starts the browser: a node
        outside its window must not hold one of the few capture slots.
        """
        base_url = get_repository_config()["base_url"]
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=10.0), event_hooks=OWN_SERVICE_HOOKS
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
            timeout=timeout, event_hooks=OWN_SERVICE_HOOKS
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
            "fortgefahren, damit das Zeitlimit für den Workflow reicht",
        }
        if budget <= 0:
            logger.warning("⚠️ Preview skipped: no time left before the workflow")
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
        """
        An image source for a URL or inline image data (data URL, bare base64).

        A URL is fetched with a client of its own: the host is a third party and
        gets no tracing headers.
        """

        async def fetch() -> tuple[bytes, str]:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                event_hooks=THIRD_PARTY_HOOKS,
            ) as fetch_client:
                return await resolve_preview(fetch_client, preview)

        return fetch

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


_repository_service: Optional[RepositoryService] = None


def get_repository_service() -> Optional[RepositoryService]:
    """Get repository service singleton (requires credentials in environment)."""
    global _repository_service

    if _repository_service is None:
        from ..config import get_settings

        settings = get_settings()

        username = settings.wlo_guest_username
        password = settings.wlo_guest_password

        if username and password:
            _repository_service = RepositoryService(username, password)
        else:
            return None

    return _repository_service
