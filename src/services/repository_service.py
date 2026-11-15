"""Repository service for uploading metadata to WLO edu-sharing repository."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from ..config import get_settings
from ..metrics import OWN_SERVICE_HOOKS
from ..utils.tls_context import shared_tls_context
from ..utils.schema_loader import get_repo_fields, get_schema_fields
from .object_shapes import find_object_shape_violations
from .repository_curation import (
    extract_collection_ids,
    run_workflow_steps,
    set_collections,
)

# The parts of the service (audit 14.11., Q-07: the editing window, the
# preview upload and the unfinished write are mixins like the first three).
from .repository_cut_off import CutOffMixin
from .repository_edit_window import EditWindowMixin
from .repository_metadata import MetadataWriteMixin
from .repository_nodes import NodeMixin, url_lock_key, url_locks
from .repository_preview_upload import PreviewUploadMixin, preview_reserve
from .repository_review import ReviewMixin

# Re-exported: callers and tests import these from repository_service, as before 3.0.0.
from .repository_preview import MAX_PREVIEW_BYTES as MAX_PREVIEW_BYTES
from .repository_preview import PreviewError as PreviewError
from .repository_preview import resolve_preview as resolve_preview
from .repository_result import (
    METADATA_INVALID,
    WRITE_FAILED,
    created_result,
    duplicate_result,
    no_writable_fields_result,
    unloadable_schema_result,
    written_result,
)
from .repository_support import get_repository_config as get_repository_config

# Shared helpers the flows below use.
from .repository_support import (
    first_value,
    identifying_url,
    nothing_written,
    partial_problems,
    duplicate_skipped,
)
from .repository_support import build_auth_header as build_auth_header
from .repository_write import (
    NodeWrite,
    StepResults,
    WriteProgress,
    WriteTarget,
    upload_workflow_steps,
    with_resolved_schema,
)

# Public names this module defined before 3.0.0 (tests/test_import_compatibility.py)
# or before its split of 14.11.
from .repository_edit_window import NODE_EDIT_WINDOW as NODE_EDIT_WINDOW
from .repository_metadata import EXTENDED_TYPE_TO_NEW_LRT as EXTENDED_TYPE_TO_NEW_LRT
from .repository_nodes import DISCARD_TIMEOUT_SECONDS as DISCARD_TIMEOUT_SECONDS
from .repository_nodes import derive_node_name as derive_node_name
from .repository_preview import MAX_PREVIEW_REDIRECTS as MAX_PREVIEW_REDIRECTS
from .repository_preview import PREVIEW_USER_AGENT as PREVIEW_USER_AGENT
from .repository_support import node_created_at as node_created_at
from .repository_write import PreviewSource as PreviewSource

logger = logging.getLogger(__name__)

# The name before 3.0.0, kept so existing imports keep working.
_get_repository_config = get_repository_config


# The read-back after a finished write runs outside the deadline with this limit
# of its own: the node is complete, a slow read must not turn it into a 504.
# Deadline (100 s) + read-back + preStop (5 s) stays below the 320 s of ingress
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


def _schema_loads(context: str, version: str, schema_file: Any) -> bool:
    """Whether the schema a write names can be read (its fields are needed)."""
    try:
        get_schema_fields(context, version, schema_file)
    except Exception as e:
        logger.warning(f"⚠️ Schema {str(schema_file)[:120]!r} not loadable: {e}")
        return False
    return True


class RepositoryService(
    NodeMixin,
    MetadataWriteMixin,
    ReviewMixin,
    EditWindowMixin,
    PreviewUploadMixin,
    CutOffMixin,
):
    """
    Service for uploading metadata to WLO edu-sharing repository.

    Write steps (_write_steps / _after_metadata):
    1. Check for duplicates (by ccm:wwwurl: search and the newest of the
       inbox), unless a node was handed over
    2. Create the node with minimal data (1 and 2 one after the other per
       URL), or check the handed-over node's editing window
    3. Add the aspects the special fields need, then set the metadata
    4. Write the extended fields (optional)
    5. Store the preview image (optional; within what the deadline leaves
       for the steps after it)
    6. Reference the node in collections (optional; from the first request
       on the node is never discarded)
    7. Run the review workflow steps (optional; the node is never discarded)
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

        target = WriteTarget(clean_metadata, None, None, config["inbox_id"])
        progress = WriteProgress()
        try:
            timeout = httpx.Timeout(45.0, connect=10.0)
            async with httpx.AsyncClient(
                timeout=timeout,
                event_hooks=OWN_SERVICE_HOOKS,
                verify=shared_tls_context(),
            ) as client:
                async with asyncio.timeout_at(deadline):
                    return await self._create_only(
                        client, base_url, target, progress, check_duplicates
                    )
        except TimeoutError:
            return await self._create_cut_off(base_url, target, progress)
        except _ANSWER_MAYBE_LOST:
            if progress.created_node_id:
                return created_result(
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
        target: WriteTarget,
        progress: WriteProgress,
        check_duplicates: bool,
    ) -> dict[str, Any]:
        """The requests of POST /node: duplicate check (optional), create."""
        clean_metadata = target.clean_metadata
        duplicate, result, duplicate_warning = await self._create_unless_duplicate(
            client, base_url, target, progress, check_duplicates
        )
        if duplicate is not None:
            return await self._duplicate_result(
                client, base_url, duplicate, clean_metadata, read_back=False
            )
        if not result.get("success"):
            return result
        created = created_result(base_url, result["nodeId"], clean_metadata)
        if duplicate_warning:
            created["error"] = duplicate_skipped(duplicate_warning)
        return created

    async def _create_unless_duplicate(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        target: WriteTarget,
        progress: WriteProgress,
        check_duplicates: bool,
    ) -> tuple[Optional[dict[str, Any]], dict[str, Any], Optional[str]]:
        """
        (duplicate, create result, warning): the duplicate check, then the create.

        With the check they are one step per URL: a second request for the same
        URL waits until the first has created its node and then finds it (audit
        14.11., R-05). A duplicate comes without a create result; `warning`
        names a check that could not run.
        """
        url = identifying_url(target.clean_metadata) if check_duplicates else None
        if not url:
            created = await self._create_tracked(client, base_url, target, progress)
            return None, created, None
        async with url_locks.holding(url_lock_key(url)):
            duplicate = await self._check_duplicate(
                client, base_url, url, target.inbox_id
            )
            if duplicate.get("exists"):
                return duplicate, {}, None
            created = await self._create_tracked(client, base_url, target, progress)
            return None, created, duplicate.get("warning")

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
        Upload metadata to WLO repository: the write of /upload from keywords.

        The router builds its NodeWrite itself and calls write_node (audit
        14.11., Q-10); this signature stays for callers that name the options.

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
        steps = upload_workflow_steps(start_workflow, workflow_steps)
        return await self.write_node(
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
        Write metadata to the repository — creating a node or updating one: the
        write of /nodes from keywords (the router builds its NodeWrite itself).

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
        return await self.write_node(
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

    async def write_node(
        self, job: NodeWrite, deadline: Optional[float] = None
    ) -> dict[str, Any]:
        """
        The one write path of /upload and /nodes.

        Refuses what may not be written before the first request, then: node,
        aspects, metadata, extended fields, preview, collections, workflow,
        read-back. A node this call created is discarded when a later step fails
        or the request is cut off; at the deadline (loop time) the answer says
        what happened to it (error_code upload_timeout, audit D-04).
        """
        config = get_repository_config()
        base_url = config["base_url"]
        job = with_resolved_schema(job)
        clean_metadata = self._extract_metadata_fields(job.metadata)
        schema_file = job.metadata.get("metadataset") or None

        # Refuse before anything is written. A schema that does not load would
        # leave core.json alone to decide what is written (audit 14.11., R-04).
        if schema_file and not _schema_loads(job.context, job.version, schema_file):
            return unloadable_schema_result(job.context, job.version, schema_file)

        # A value whose shape contradicts the schema cannot be silently
        # corrected here: the caller reviewed what they sent, and the repository
        # must hold that, not a version of it nobody saw. Checked ahead of the
        # first request so no half-written node is left over.
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

        target = WriteTarget(
            clean_metadata, schema_file, repo_field_ids, config["inbox_id"]
        )
        progress = WriteProgress()
        try:
            # Longer timeout for sequential edu-sharing calls on hosted runtimes
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(45.0, connect=10.0),
                event_hooks=OWN_SERVICE_HOOKS,
                verify=shared_tls_context(),
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

    async def _write_steps(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        job: NodeWrite,
        target: WriteTarget,
        progress: WriteProgress,
        deadline: Optional[float] = None,
    ) -> dict[str, Any]:
        """The requests of one write, in the order the editorial process needs them."""
        clean_metadata = target.clean_metadata

        # 1. Check for duplicates. Skipped for a node that was handed over: the
        # check searches by ccm:wwwurl, and the node being filled in already
        # carries that URL — it would find itself and refuse the upload.
        # 2. Create the node, or check that the given one may still be written:
        # after the window it has reached the editors, and this call would
        # replace their work.
        duplicate_warning = None
        if job.node_id:
            refusal, existing = await self._edit_check(client, base_url, job.node_id)
            if refusal is not None:
                return refusal
            target.node_properties = (existing or {}).get("properties") or {}
            node_id = job.node_id
            logger.info(f"📌 Writing existing node: {node_id}")
        else:
            outcome = await self._create_unless_duplicate(
                client, base_url, target, progress, job.check_duplicates
            )
            duplicate, node_result, duplicate_warning = outcome
            if duplicate is not None:
                return await self._duplicate_result(
                    client, base_url, duplicate, clean_metadata, job.read_back
                )
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
        result = written_result(
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
        # 8. The read-back (job.read_back) follows in write_node, outside the
        # deadline: the node is complete by now.
        return result

    async def _after_metadata(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str,
        job: NodeWrite,
        target: WriteTarget,
        progress: WriteProgress,
        deadline: Optional[float] = None,
    ) -> StepResults:
        """Extended fields, preview, collections, workflow — each reported, none fatal."""
        steps = StepResults()

        # 4. Write extended data fields (bypasses repo_field filter)
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

        all_collection_ids = extract_collection_ids(
            target.clean_metadata, job.collection_ids
        )

        # 5. Preview before the workflow, so whoever picks the node out of the
        # queue sees it
        if job.preview_source:
            reserve = preview_reserve(len(all_collection_ids), len(job.workflow_steps))
            steps.preview = await self._bounded_preview(
                client, base_url, node_id, job.preview_source, deadline, reserve
            )

        # 6. Reference the node in collections (from metadata and/or request).
        # Last before the workflow: a deadline during the steps above discards
        # a node this call created, and a reference set before them stayed
        # behind in the collection, pointing at it (audit 13.11., L-18). From
        # the first request on the node is kept (audit 14.11., R-01).
        if all_collection_ids:
            progress.collection_ids = all_collection_ids
            progress.collection_results = []
            steps.collections = await set_collections(
                client,
                self._auth_header,
                base_url,
                node_id,
                all_collection_ids,
                progress.collection_results,
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

    async def _duplicate_result(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        duplicate: dict[str, Any],
        clean_metadata: dict[str, Any],
        read_back: bool,
    ) -> dict[str, Any]:
        """The refusal for a URL the repository already holds, read back if asked."""
        result = duplicate_result(base_url, duplicate, clean_metadata)
        existing = duplicate.get("nodeId")
        if read_back and existing:
            result["node_full"] = await self._fetch_full_node(
                client, base_url, existing
            )
        return result

    async def _create_tracked(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        target: WriteTarget,
        progress: WriteProgress,
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
                target.repo_field_ids,
            )
            progress.maybe_created = progress.created_node_id is None
            raise
        progress.creating_since = None
        if node_result.get("success"):
            progress.created_node_id = node_result["nodeId"]
            logger.info(f"✅ Created node: {node_result['nodeId']}")
        return node_result


_repository_service: Optional[RepositoryService] = None


def get_repository_service() -> Optional[RepositoryService]:
    """Get repository service singleton (requires credentials in environment)."""
    global _repository_service

    if _repository_service is None:
        settings = get_settings()

        username = settings.wlo_guest_username
        password = settings.wlo_guest_password

        if username and password:
            _repository_service = RepositoryService(username, password)
        else:
            return None

    return _repository_service
