"""Repository service for uploading metadata to WLO edu-sharing repository."""

import base64
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote, urljoin

import httpx

from ..utils.public_url import PublicUrlError, require_public_http_url
from ..utils.request_id import TRACE_EVENT_HOOKS
from ..utils.schema_loader import get_repo_fields, get_content_type_uri
from .field_normalizer import find_object_shape_violations
from .repository_curation import (
    DEFAULT_WORKFLOW_STATUS,
    extract_collection_ids,
    fetch_workflow_history,
    run_workflow_steps,
    set_collections,
)
from .repository_diff import compute_diff, properties_to_flat, unresolved_values
from .repository_licenses import transform_license
from .repository_values import (
    extract_geo_coordinates,
    normalize_for_repo,
    transform_author_to_vcard,
    transform_lrt,
)

logger = logging.getLogger(__name__)


# Rolling back a half-finished upload runs *after* that upload has already spent
# its budget — typically after its own 45s timeout, inside a serverless
# invocation with a strict duration limit. A generous timeout here
# gets killed exactly in the slow-repository case the rollback exists for, and
# the orphaned node survives anyway. It is a single DELETE; keep it short.
DISCARD_TIMEOUT_SECONDS = 5.0

# How long after its creation a node may still be rewritten through this service.
# Past that point the content has been in the editorial queue long enough for a
# human to have worked on it, and an automated write would replace that work
# without anyone being told. The window is deliberately short: it covers the
# session that produced the node, not later revisions.
NODE_EDIT_WINDOW = timedelta(hours=2)

# Largest preview image this service will fetch or decode. edu-sharing renders a
# thumbnail from it, so nothing near this size is ever needed — the limit is
# there to stop a wrong URL from pulling an arbitrary download into memory.
MAX_PREVIEW_BYTES = 10 * 1024 * 1024

# Each hop of a preview redirect chain is checked; a longer chain is refused.
MAX_PREVIEW_REDIRECTS = 5

# Sent when fetching a preview image. Several of the hosts these images live on —
# Wikimedia among them — answer 403 to a client that identifies itself only by
# its HTTP library, so a name is not optional here.
PREVIEW_USER_AGENT = "MetadataAgentAPI/2.0 (preview fetch)"

# Leading bytes of the image formats a preview may arrive in. Consulted when
# neither the data URL nor the serving host names a usable type.
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class PreviewError(Exception):
    """The value given as a preview could not be turned into an image."""


def _sniff_image_mimetype(image: bytes) -> str:
    """
    Determine an image's media type from its first bytes.

    Falls back to PNG: edu-sharing stores the bytes either way, and a wrong
    label on an otherwise valid image is a better outcome than refusing it.
    """
    for signature, mimetype in _IMAGE_SIGNATURES:
        if image.startswith(signature):
            return mimetype
    if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "image/webp"
    if image.lstrip()[:4].lower() == b"<svg":
        return "image/svg+xml"
    return "image/png"


def _decode_preview_bytes(value: str) -> tuple[bytes, str]:
    """
    Decode an inline preview — a data URL or bare base64 — into image bytes.

    The media type named by a data URL is trusted only as far as being an
    image; anything else is worked out from the decoded bytes, because a
    hand-built data URL routinely carries the wrong one.
    """
    payload = value
    declared: Optional[str] = None

    if value[:5].lower() == "data:":
        header, _, payload = value.partition(",")
        if not payload:
            raise PreviewError("Data-URL ohne Bilddaten — nach dem Komma steht nichts.")
        media = header[5:].split(";")[0].strip().lower()
        if media.startswith("image/"):
            declared = media

    try:
        image = base64.b64decode("".join(payload.split()), validate=True)
    except Exception as e:
        raise PreviewError(
            f"Preview ist weder eine http(s)-URL noch gültiges Base64: {e}"
        ) from e

    if not image:
        raise PreviewError("Preview ist leer.")
    if len(image) > MAX_PREVIEW_BYTES:
        raise PreviewError(
            f"Preview ist {len(image) // 1024} KB groß — erlaubt sind "
            f"{MAX_PREVIEW_BYTES // 1024} KB."
        )

    return image, declared or _sniff_image_mimetype(image)


async def resolve_preview(client: httpx.AsyncClient, value: str) -> tuple[bytes, str]:
    """
    Turn the `preview` parameter into image bytes and their media type.

    Two forms arrive here and are told apart by the value itself: an http(s)
    URL, which is fetched, and an inline image as a data URL or bare base64,
    which is decoded. Raises PreviewError with a message naming the form that
    was recognised, so a caller who meant the other one can see which.
    """
    candidate = value.strip()
    if not candidate:
        raise PreviewError("Preview ist leer.")

    lowered = candidate.lower()
    if not lowered.startswith(("http://", "https://")):
        if "://" in candidate[:20]:
            raise PreviewError(
                "Preview-URL muss mit http:// oder https:// beginnen — andere "
                "Schemas werden nicht geladen."
            )
        return _decode_preview_bytes(candidate)

    # Redirects are followed by hand: every hop has to be a public target, or a
    # public URL could bounce this service into an internal network.
    current_url = candidate
    for _ in range(MAX_PREVIEW_REDIRECTS + 1):
        try:
            await require_public_http_url(current_url)
        except PublicUrlError as e:
            raise PreviewError(f"Preview-URL ist nicht öffentlich: {e}") from e

        try:
            async with client.stream(
                "GET",
                current_url,
                headers={
                    "Accept": "image/*",
                    # The size limit counts bytes as they arrive (see
                    # _read_preview_body); a compressed answer is refused.
                    "Accept-Encoding": "identity",
                    "User-Agent": PREVIEW_USER_AGENT,
                },
                follow_redirects=False,
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise PreviewError("Preview-Weiterleitung ohne Ziel.")
                    try:
                        current_url = urljoin(current_url, location)
                    except ValueError as e:
                        # httpx accepts some targets urllib cannot parse.
                        raise PreviewError(
                            f"Preview-Weiterleitung mit ungültigem Ziel: {location!r}"
                        ) from e
                    continue

                if response.status_code != 200:
                    raise PreviewError(
                        "Preview-Bild konnte nicht geladen werden: "
                        f"HTTP {response.status_code}"
                    )

                image = await _read_preview_body(response)
                served = (
                    response.headers.get("content-type", "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                break
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            # InvalidURL is not an HTTPError subclass.
            raise PreviewError(
                f"Preview-Bild konnte nicht geladen werden: {type(e).__name__}: {e}"
            ) from e
    else:
        raise PreviewError("Preview-URL leitet zu oft weiter.")

    if not image:
        raise PreviewError("Preview-URL lieferte keine Daten.")

    mimetype = served if served.startswith("image/") else _sniff_image_mimetype(image)
    return image, mimetype


async def _read_preview_body(response: httpx.Response) -> bytes:
    """
    Read an image response, refusing it as soon as it exceeds the limit.

    httpx decodes a Content-Encoding before the bytes reach this loop, so one
    chunk of stacked gzip could inflate far past the limit before it is
    counted. A compressed answer is therefore refused — the request asks for
    identity — and the bytes counted are the bytes received.
    """
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding not in ("", "identity"):
        raise PreviewError(
            f"Preview-Bild kam komprimiert ({encoding}) — unterstützt wird nur "
            "eine unkomprimierte Antwort."
        )

    too_large = (
        "Preview-Bild ist größer als erlaubt — höchstens "
        f"{MAX_PREVIEW_BYTES // 1024} KB."
    )
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_PREVIEW_BYTES:
        raise PreviewError(too_large)

    image = bytearray()
    async for chunk in response.aiter_bytes():
        image.extend(chunk)
        if len(image) > MAX_PREVIEW_BYTES:
            raise PreviewError(too_large)
    return bytes(image)


def build_auth_header(username: str, password: str) -> Optional[str]:
    """
    Build a Basic Auth header for the WLO service account.

    Returns None when either credential is missing, so callers can fall back to
    anonymous access instead of sending a header for empty credentials.
    """
    if not username or not password:
        return None
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {encoded}"


def _get_repository_config() -> dict:
    """Build repository config from settings (single configured URL)."""
    from ..config import get_settings

    settings = get_settings()

    # Derive upload base URL from settings (strip '/rest' suffix if present,
    # because the upload endpoints append '/rest/...' themselves)
    base = settings.repository_url.rstrip("/")
    if base.endswith("/rest"):
        base = base[: -len("/rest")]

    return {
        "base_url": base,
        "inbox_id": settings.wlo_inbox_id,
    }


# Mapping: ccm:oeh_extendedType URI → oeh:new_lrt URI
# Sets the learning resource type (LRT) based on the detected content type.
EXTENDED_TYPE_TO_NEW_LRT = {
    "http://w3id.org/openeduhub/vocabs/contentTypes/event": "http://w3id.org/openeduhub/vocabs/new_lrt/955590ae-5f06-4513-98e9-91dfa8d5a05e",
    "http://w3id.org/openeduhub/vocabs/contentTypes/source": "http://w3id.org/openeduhub/vocabs/new_lrt/3869b453-d3c1-4b34-8f25-9127e9d68766",
    "http://w3id.org/openeduhub/vocabs/contentTypes/education_offer": "http://w3id.org/openeduhub/vocabs/new_lrt/03ab835b-c39c-48d1-b5af-7611de2f6464",
    "http://w3id.org/openeduhub/vocabs/contentTypes/tool_service": "http://w3id.org/openeduhub/vocabs/new_lrt/cefccf75-cba3-427d-9a0f-35b4fedcbba1",
    "http://w3id.org/openeduhub/vocabs/contentTypes/didactic_concepts": "http://w3id.org/openeduhub/vocabs/new_lrt/0a79a1d0-583b-47ce-86a7-517ab352d796",
    "http://w3id.org/openeduhub/vocabs/contentTypes/learning_material": "http://w3id.org/openeduhub/vocabs/new_lrt/1846d876-d8fd-476a-b540-b8ffd713fedb",
}


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


def _first(value: Any) -> Any:
    """Unwrap a repository property: they are arrays, the answer wants one value."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _no_repo_fields(context: str, version: str, schema_file: Optional[str]) -> dict:
    """
    The answer when the schemas name no field that may be written.

    That only happens when the schema could not be loaded (unknown context or
    version, unreadable file). Writing anyway would create a node holding just
    its title and report it as a success.
    """
    schemas = f"core.json + {schema_file}" if schema_file else "core.json"
    return {
        "success": False,
        "step": "schema",
        "error": (
            f"Für Kontext '{context}', Version '{version}' ({schemas}) ist kein "
            "Repository-Feld bekannt — das Schema ließ sich nicht laden. Es wurde "
            "nichts geschrieben."
        ),
    }


def duplicate_skipped(warning: str) -> str:
    """
    Message for a duplicate check that could not run.

    The upload goes ahead — a flaky search must not block every upload — but
    the caller has to know that nobody looked for an existing copy.
    """
    return f"Dublettenprüfung nicht möglich ({warning}) — Node ohne Prüfung angelegt"


def _nothing_written(metadata_result: dict) -> bool:
    """True when the repository took none of the fields it was sent."""
    return metadata_result.get("fields_written", 0) == 0 and not metadata_result.get(
        "success", True
    )


def _partial_problems(
    metadata_result: dict,
    aspect_problem: Optional[str],
    extended_result: Optional[dict],
    collection_result: Optional[dict],
    workflow_result: Optional[dict],
    preview_result: Optional[dict] = None,
) -> list[str]:
    """
    Name every step that failed although the node itself was written.

    Each of them leaves the node short of what was sent — a missing collection
    reference, a skipped review state, coordinates the repository dropped — and
    the caller can only act on it if the answer says so.
    """
    problems = []
    field_errors = metadata_result.get("field_errors") or []
    if field_errors:
        problems.append(
            f"{len(field_errors)} Feld(er) konnten nicht geschrieben werden"
        )
    if aspect_problem:
        problems.append(aspect_problem)
    if extended_result is not None and not extended_result.get("success"):
        problems.append(
            "Erweiterte Daten (ccm:oeh_extendedType/Data/Text) konnten nicht "
            "geschrieben werden"
        )
    failed_collections = [
        str(entry["collectionId"])
        for entry in (collection_result or {}).get("results", [])
        if not entry["success"]
    ]
    if failed_collections:
        problems.append(
            f"Sammlung(en) nicht verknüpft: {', '.join(failed_collections)}"
        )
    failed_steps = [
        step["status"]
        for step in (workflow_result or {}).get("steps", [])
        if not step["success"]
    ]
    if failed_steps:
        problems.append(f"Workflow-Schritt(e) nicht gesetzt: {', '.join(failed_steps)}")
    if preview_result is not None and not preview_result["success"]:
        problems.append(f"Preview: {preview_result['error']}")
    return problems


def _node_url(metadata: dict) -> Optional[str]:
    """
    The URL a node is identified by, as one string.

    _create_node writes schema:url into ccm:wwwurl for schemas without the
    latter, so the duplicate check has to look at the same fallback — and the
    search API takes a list of strings, not a list holding a list.
    """
    return _first(metadata.get("ccm:wwwurl") or metadata.get("schema:url")) or None


def node_created_at(node: dict[str, Any]) -> Optional[datetime]:
    """
    Read the creation time of a repository node as an aware UTC datetime.

    edu-sharing reports it twice: `createdAt` beside the node as ISO 8601, and
    `cm:created` among the properties as epoch milliseconds. Both are read
    because the second survives a `propertyFilter` that drops the first.

    Returns None when neither is present or parsable — a node whose age cannot
    be established, which callers guarding on age must not treat as young.
    """
    created = node.get("createdAt")
    if isinstance(created, str) and created.strip():
        try:
            parsed = datetime.fromisoformat(created.strip().replace("Z", "+00:00"))
            return (
                parsed
                if parsed.tzinfo is not None
                else parsed.replace(tzinfo=timezone.utc)
            )
        except ValueError:
            pass

    millis = _first((node.get("properties") or {}).get("cm:created"))
    try:
        return datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


class RepositoryService:
    """
    Service for uploading metadata to WLO edu-sharing repository.

    Workflow:
    1. Check for duplicates (by ccm:wwwurl)
    2. Create node with minimal data
    3. Set full metadata
    4. Add to collections (optional)
    5. Run the review workflow steps
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
    ) -> dict[str, Any]:
        """
        Create a node and stop there, so the caller has an id to work with.

        The first half of `upload_metadata`, on its own: the same five fields the
        upload path puts on a new node (title, description, keywords, url,
        language) plus `ccm:linktype`. Everything beyond that needs the schema
        filter, which is what handing the id back to `/upload` is for.

        A title is required. A node without one is unfindable in the inbox, and
        the point of this call is to get something a human can refer to.
        """
        config = _get_repository_config()
        base_url = config["base_url"]

        clean_metadata = self._extract_metadata_fields(metadata)
        title = clean_metadata.get("cclom:title") or clean_metadata.get("schema:name")
        if not title:
            return {
                "success": False,
                "error": "cclom:title fehlt — ein Node ohne Titel ist im Eingangsordner nicht auffindbar.",
            }

        try:
            timeout = httpx.Timeout(45.0, connect=10.0)
            async with httpx.AsyncClient(
                timeout=timeout, event_hooks=TRACE_EVENT_HOOKS
            ) as client:
                duplicate_warning = None
                if check_duplicates:
                    url = _node_url(clean_metadata)
                    if url:
                        duplicate = await self._check_duplicate(client, base_url, url)
                        if duplicate.get("exists"):
                            existing = duplicate.get("nodeId")
                            return {
                                "success": False,
                                "duplicate": True,
                                "node": {
                                    "nodeId": existing,
                                    "title": duplicate.get("title"),
                                    "wwwurl": url,
                                    "repositoryUrl": f"{base_url}/components/render/{existing}",
                                },
                                "error": f'URL existiert bereits: "{duplicate.get("title")}"',
                            }
                        duplicate_warning = duplicate.get("warning")

                result = await self._create_node(
                    client, base_url, config["inbox_id"], clean_metadata
                )
                if not result.get("success"):
                    return result

                node_id = result["nodeId"]
                logger.info(f"✅ Created node: {node_id}")
                created = {
                    "success": True,
                    "node": {
                        "nodeId": node_id,
                        "title": _first(clean_metadata.get("cclom:title")),
                        "wwwurl": _node_url(clean_metadata),
                        "repositoryUrl": f"{base_url}/components/render/{node_id}",
                    },
                }
                if duplicate_warning:
                    created["error"] = duplicate_skipped(duplicate_warning)
                return created
        except Exception as e:
            logger.error(f"❌ Node creation failed: {type(e).__name__}: {e}")
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

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
    ) -> dict[str, Any]:
        """
        Upload metadata to WLO repository.

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

        Returns:
            Upload result with nodeId, success status, etc.
        """
        config = _get_repository_config()

        base_url = config["base_url"]
        inbox_id = config["inbox_id"]

        # Extract metadata fields (remove processing info, etc.)
        clean_metadata = self._extract_metadata_fields(metadata)

        # Determine which schema was used from metadataset field
        schema_file = metadata.get("metadataset") or None

        # Refuse before anything is written. A value whose shape contradicts the
        # schema cannot be silently corrected here: the caller reviewed what they
        # sent, and the repository must hold that, not a version of it nobody saw.
        # Checked ahead of the first request so no half-written node is left over.
        if schema_file:
            violations = find_object_shape_violations(
                clean_metadata, context, version, schema_file
            )
            if violations:
                return {
                    "success": False,
                    "error": "Metadaten wurden nicht geschrieben: "
                    + "; ".join(v["message"] for v in violations),
                    "violations": violations,
                }

        # Load repo-eligible fields from schemas
        repo_field_ids = get_repo_fields(context, version, schema_file)
        logger.info(f"📋 Repo fields from schema: {len(repo_field_ids)} fields")
        if schema_file:
            logger.info(f"   Schemas: core.json + {schema_file}")
        if not repo_field_ids:
            return _no_repo_fields(context, version, schema_file)

        # Remembered so a failure after this point can undo the half-finished
        # node. Only ever set for a node this call created: a node the caller
        # handed over may hold their content and is not ours to bin.
        created_node_id: Optional[str] = None
        reuse_node_id = node_id or None

        try:
            # Longer timeout for sequential edu-sharing calls on hosted runtimes
            timeout = httpx.Timeout(45.0, connect=10.0)
            async with httpx.AsyncClient(
                timeout=timeout, event_hooks=TRACE_EVENT_HOOKS
            ) as client:
                # 1. Check for duplicates. Skipped for a node that was handed
                # over: the check searches by ccm:wwwurl, and the node being
                # filled in already carries that URL — it would find the target
                # itself and refuse the upload as its own duplicate.
                duplicate_warning = None
                if check_duplicates and not reuse_node_id:
                    url = _node_url(clean_metadata)
                    if url:
                        duplicate = await self._check_duplicate(client, base_url, url)
                        if duplicate.get("exists"):
                            node_id = duplicate.get("nodeId")
                            dup_result = {
                                "success": False,
                                "duplicate": True,
                                "node": {
                                    "nodeId": node_id,
                                    "title": duplicate.get("title"),
                                    "description": duplicate.get("description"),
                                    "wwwurl": url,
                                    "repositoryUrl": f"{base_url}/components/render/{node_id}",
                                },
                                "error": f'URL existiert bereits: "{duplicate.get("title")}"',
                            }
                            if return_full_node and node_id:
                                dup_result["node_full"] = await self._fetch_full_node(
                                    client, base_url, node_id
                                )
                            return dup_result
                        duplicate_warning = duplicate.get("warning")

                # 2. Create node with minimal data — unless one was handed over
                if reuse_node_id:
                    # Same guard as save_node: after the window the node has
                    # reached the editors, and this call would replace their work.
                    refusal = await self._reject_if_too_old(
                        client, base_url, reuse_node_id
                    )
                    if refusal is not None:
                        return refusal
                    node_id = reuse_node_id
                    logger.info(f"📌 Using existing node: {node_id}")
                else:
                    node_result = await self._create_node(
                        client, base_url, inbox_id, clean_metadata
                    )
                    if not node_result.get("success"):
                        return node_result

                    node_id = node_result["nodeId"]
                    created_node_id = node_id
                    logger.info(f"✅ Created node: {node_id}")

                # 2b. Add required aspects for special fields
                aspect_problem = await self._ensure_aspects(
                    client, base_url, node_id, clean_metadata
                )

                # 3. Set full metadata (only repo_field=true fields from schemas)
                metadata_result = await self._set_metadata(
                    client, base_url, node_id, clean_metadata, repo_field_ids
                )
                # Collections and workflow would put a node holding only its
                # title in front of editors — stop here instead.
                if _nothing_written(metadata_result):
                    return await self._metadata_rejected(
                        base_url, created_node_id, metadata_result
                    )

                # 4. Reference the node in collections (from metadata and/or request)
                all_collection_ids = extract_collection_ids(
                    clean_metadata, collection_ids
                )
                collection_result = None
                if all_collection_ids:
                    collection_result = await set_collections(
                        client,
                        self._auth_header,
                        base_url,
                        node_id,
                        all_collection_ids,
                    )

                # 5. Write extended data fields (bypasses repo_field filter)
                extended_result = None
                if write_extended_data:
                    extended_result = await self._write_extended_fields(
                        client,
                        base_url,
                        node_id,
                        metadata,
                        context,
                        version,
                        extended_text,
                    )

                # 6. Run the review workflow steps
                workflow_result = None
                if start_workflow:
                    workflow_result = await run_workflow_steps(
                        client,
                        self._auth_header,
                        base_url,
                        node_id,
                        workflow_steps or [DEFAULT_WORKFLOW_STATUS],
                        workflow_comment,
                        workflow_receiver,
                    )

                # Extract key metadata for response (with fallbacks for organization schema)
                title = _first(
                    clean_metadata.get("cclom:title")
                    or clean_metadata.get("schema:name")
                )
                description = _first(
                    clean_metadata.get("cclom:general_description")
                    or clean_metadata.get("schema:description")
                )
                wwwurl = _first(
                    clean_metadata.get("ccm:wwwurl") or clean_metadata.get("schema:url")
                )

                result = {
                    "success": True,
                    # Which schema decided what may be written. Without it a
                    # caller cannot tell a complete upload from one that quietly
                    # fell back to core.json and dropped the type-specific
                    # fields — both answer 200.
                    "schema_used": schema_file,
                    "repo_fields_available": len(repo_field_ids),
                    # False when the caller handed the node over. Both answer 200
                    # with the same nodeId, so nothing else tells them apart.
                    "node_created": created_node_id is not None,
                    "node": {
                        "nodeId": node_id,
                        "title": title,
                        "description": description[:200] + "..."
                        if description and len(description) > 200
                        else description,
                        "wwwurl": wwwurl,
                        "repositoryUrl": f"{base_url}/components/render/{node_id}",
                    },
                    "fields_written": metadata_result.get("fields_written", 0),
                    "fields_skipped": metadata_result.get("fields_skipped", 0),
                }

                if collection_result is not None:
                    result["collections"] = collection_result["results"]
                if workflow_result is not None:
                    result["workflow"] = workflow_result["steps"]

                field_errors = metadata_result.get("field_errors", [])
                if field_errors:
                    result["field_errors"] = field_errors
                problems = _partial_problems(
                    metadata_result,
                    aspect_problem,
                    extended_result,
                    collection_result,
                    workflow_result,
                )
                if duplicate_warning:
                    problems.insert(0, duplicate_skipped(duplicate_warning))
                if problems:
                    result["error"] = "; ".join(problems)

                # 7. Read the finished node back for callers that cannot fetch it
                # themselves (e.g. guest clients without repository access)
                if return_full_node:
                    result["node_full"] = await self._fetch_full_node(
                        client, base_url, node_id
                    )

                return result

        except httpx.TimeoutException as e:
            logger.error(f"❌ Repository upload timed out: {e}")
            return await self._failed(
                base_url,
                created_node_id,
                f"Timeout bei der Verbindung zum Repository: {e}",
            )
        except httpx.ConnectError as e:
            logger.error(f"❌ Repository connection failed: {e}")
            return await self._failed(
                base_url,
                created_node_id,
                f"Verbindung zum Repository fehlgeschlagen: {e}",
            )
        except Exception as e:
            logger.error(f"❌ Repository upload failed: {type(e).__name__}: {e}")
            return await self._failed(
                base_url, created_node_id, f"{type(e).__name__}: {e}"
            )

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
        node = await self._fetch_full_node(client, base_url, node_id)
        node_info = {
            "nodeId": node_id,
            "repositoryUrl": f"{base_url}/components/render/{node_id}",
        }

        if node is None:
            return {
                "success": False,
                "error_code": "node_not_found",
                "error": (
                    f"Node {node_id} konnte nicht gelesen werden — er existiert "
                    "nicht oder der Dienst hat keinen Zugriff darauf."
                ),
                "node": node_info,
            }

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
            }

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
            }

        return None

    async def _set_preview(
        self, client: httpx.AsyncClient, base_url: str, node_id: str, preview: str
    ) -> dict[str, Any]:
        """
        Put a preview image on the node, from a URL or from inline image data.

        Never raises: the metadata is already written when this runs, so a
        preview that cannot be fetched, decoded or stored is reported in its own
        result rather than undoing the write.
        """
        try:
            image, mimetype = await resolve_preview(client, preview)
        except PreviewError as e:
            logger.warning(f"⚠️ Preview rejected: {e}")
            return {"success": False, "error": str(e)}

        extension = mimetype.rsplit("/", 1)[-1].replace("svg+xml", "svg")
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
            logger.warning(f"⚠️ Preview upload failed: {type(e).__name__}: {e}")
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

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
            "error": f"HTTP {response.status_code}: {response.text[:200]}",
        }

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
    ) -> dict[str, Any]:
        """
        Write metadata to the repository — creating a node or updating one.

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
        config = _get_repository_config()
        base_url = config["base_url"]
        inbox_id = config["inbox_id"]

        clean_metadata = self._extract_metadata_fields(metadata)

        # Which type schema applies on top of core.json, and therefore which
        # fields may reach the repository at all.
        schema_file = metadata.get("metadataset") or None

        # Same refusal as in upload_metadata, and for the same reason: what the
        # caller reviewed is what gets written, or nothing does. Before the first
        # request, so an update never half-lands on an existing node.
        if schema_file:
            violations = find_object_shape_violations(
                clean_metadata, context, version, schema_file
            )
            if violations:
                return {
                    "success": False,
                    "error": "Metadaten wurden nicht geschrieben: "
                    + "; ".join(v["message"] for v in violations),
                    "violations": violations,
                }

        repo_field_ids = get_repo_fields(context, version, schema_file)
        logger.info(f"📋 Repo fields from schema: {len(repo_field_ids)} fields")
        if not repo_field_ids:
            return _no_repo_fields(context, version, schema_file)

        # Only ever set for a node this call created. A node handed over by the
        # caller holds their content and is not ours to bin on failure.
        created_node_id: Optional[str] = None

        try:
            # Longer timeout for the sequence of edu-sharing calls below
            timeout = httpx.Timeout(45.0, connect=10.0)
            async with httpx.AsyncClient(
                timeout=timeout, event_hooks=TRACE_EVENT_HOOKS
            ) as client:
                # 1. Create the node, or check that the given one may be edited
                if node_id:
                    refusal = await self._reject_if_too_old(client, base_url, node_id)
                    if refusal is not None:
                        return refusal
                    logger.info(f"📌 Updating existing node: {node_id}")
                else:
                    node_result = await self._create_node(
                        client, base_url, inbox_id, clean_metadata
                    )
                    if not node_result.get("success"):
                        return node_result

                    node_id = node_result["nodeId"]
                    created_node_id = node_id
                    logger.info(f"✅ Created node: {node_id}")

                # 2. Add the aspects the special fields need before writing them
                aspect_problem = await self._ensure_aspects(
                    client, base_url, node_id, clean_metadata
                )

                # 3. Write the metadata the schema allows
                metadata_result = await self._set_metadata(
                    client, base_url, node_id, clean_metadata, repo_field_ids
                )
                # Collections, workflow and preview would put a node holding
                # only its title in front of editors — stop here instead.
                if _nothing_written(metadata_result):
                    return await self._metadata_rejected(
                        base_url, created_node_id, metadata_result
                    )

                # 4. Reference the node in collections (from metadata and request)
                all_collection_ids = extract_collection_ids(
                    clean_metadata, collection_ids
                )
                collection_result = None
                if all_collection_ids:
                    collection_result = await set_collections(
                        client,
                        self._auth_header,
                        base_url,
                        node_id,
                        all_collection_ids,
                    )

                # 5. Write the extended fields (they bypass the repo_field filter)
                extended_result = None
                if write_extended_data:
                    extended_result = await self._write_extended_fields(
                        client,
                        base_url,
                        node_id,
                        metadata,
                        context,
                        version,
                        extended_text,
                    )

                # 6. Set the preview image — before the workflow, so whoever
                # picks the node up out of the queue already sees it
                preview_result = None
                if preview:
                    preview_result = await self._set_preview(
                        client, base_url, node_id, preview
                    )

                # 7. Run the requested workflow states, each its own history entry
                workflow_result = None
                if workflow_steps:
                    workflow_result = await run_workflow_steps(
                        client,
                        self._auth_header,
                        base_url,
                        node_id,
                        workflow_steps,
                    )

                title = _first(
                    clean_metadata.get("cclom:title")
                    or clean_metadata.get("schema:name")
                )
                description = _first(
                    clean_metadata.get("cclom:general_description")
                    or clean_metadata.get("schema:description")
                )
                wwwurl = _first(
                    clean_metadata.get("ccm:wwwurl") or clean_metadata.get("schema:url")
                )

                result: dict[str, Any] = {
                    "success": True,
                    "schema_used": schema_file,
                    "repo_fields_available": len(repo_field_ids),
                    "node_created": created_node_id is not None,
                    "node": {
                        "nodeId": node_id,
                        "title": title,
                        "description": description[:200] + "..."
                        if description and len(description) > 200
                        else description,
                        "wwwurl": wwwurl,
                        "repositoryUrl": f"{base_url}/components/render/{node_id}",
                    },
                    "fields_written": metadata_result.get("fields_written", 0),
                    "fields_skipped": metadata_result.get("fields_skipped", 0),
                }

                if collection_result is not None:
                    result["collections"] = collection_result["results"]
                if preview_result is not None:
                    result["preview"] = preview_result
                if workflow_result is not None:
                    result["workflow"] = workflow_result["steps"]

                # Partial failures of a write that otherwise went through: they
                # name what is missing without claiming the node was untouched.
                field_errors = metadata_result.get("field_errors", [])
                if field_errors:
                    result["field_errors"] = field_errors
                problems = _partial_problems(
                    metadata_result,
                    aspect_problem,
                    extended_result,
                    collection_result,
                    workflow_result,
                    preview_result,
                )
                if problems:
                    result["error"] = "; ".join(problems)

                # 8. Read the node back — this endpoint always answers with it
                result["node_full"] = await self._fetch_full_node(
                    client, base_url, node_id
                )

                return result

        except httpx.TimeoutException as e:
            logger.error(f"❌ Repository write timed out: {e}")
            return await self._failed(
                base_url,
                created_node_id,
                f"Timeout bei der Verbindung zum Repository: {e}",
            )
        except httpx.ConnectError as e:
            logger.error(f"❌ Repository connection failed: {e}")
            return await self._failed(
                base_url,
                created_node_id,
                f"Verbindung zum Repository fehlgeschlagen: {e}",
            )
        except Exception as e:
            logger.error(f"❌ Repository write failed: {type(e).__name__}: {e}")
            return await self._failed(
                base_url, created_node_id, f"{type(e).__name__}: {e}"
            )

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
        self, base_url: str, created_node_id: Optional[str], error: str
    ) -> dict[str, Any]:
        """
        Build the error response and undo a half-finished upload.

        The upload is create-then-populate. When the second half never runs, the
        node stays in the inbox carrying only its title — invisible to the
        caller, who sees success=false and retries, leaving one more behind
        every time. Discarding it keeps a failed upload from becoming litter.
        """
        result: dict[str, Any] = {"success": False, "error": error}
        if not created_node_id:
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

    async def _discard_node(self, base_url: str, node_id: str) -> bool:
        """
        Move a node to the recycle bin. Never raises.

        Called while handling another error, so its own failure must not replace
        the original one — it is reported alongside instead.
        """
        url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}?recycle=true"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(DISCARD_TIMEOUT_SECONDS),
                event_hooks=TRACE_EVENT_HOOKS,
            ) as client:
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

    def _extract_metadata_fields(self, metadata: dict) -> dict:
        """Extract only metadata fields, removing processing/system info.

        Handles two formats:
        - Flat (from /generate API): fields at root level alongside system keys
        - Nested (from web component export): fields inside a 'metadata' sub-dict
        """
        # If there's a nested 'metadata' dict, unwrap it (web component export format)
        if "metadata" in metadata and isinstance(metadata.get("metadata"), dict):
            return {
                k: v for k, v in metadata["metadata"].items() if not k.startswith("_")
            }

        # Flat format: strip system and meta keys
        excluded_keys = {
            "contextName",
            "schemaVersion",
            "metadataset",
            "metadataset_uri",
            "language",
            "exportedAt",
            "processing",
            "preview_image_url",
        }
        return {
            k: v
            for k, v in metadata.items()
            if k not in excluded_keys and not k.startswith("_")
        }

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
            title = _first(clean_metadata.get("cclom:title"))
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
            error_text = response.text[:500]
            logger.error(
                f"❌ Create node failed: {response.status_code} - {error_text}"
            )
            return {
                "success": False,
                "error": f"Create node failed: {response.status_code} - {error_text}",
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
                    f"⚠️ Read-back of node {node_id} failed: HTTP {response.status_code}"
                )
                return None
            return response.json().get("node")
        except Exception as e:
            logger.warning(
                f"⚠️ Read-back of node {node_id} failed: {type(e).__name__}: {e}"
            )
            return None

    async def _set_metadata(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str,
        metadata: dict,
        repo_field_ids: set[str] | None = None,
    ) -> dict:
        """
        Set full metadata on node using dynamically loaded repo fields from schemas.

        Strategy:
        1. Filter metadata to only include fields with repo_field=true in schemas
        2. Try bulk update with all fields
        3. If bulk fails, retry field-by-field to identify problematic fields
        4. Report per-field errors
        """
        metadata_url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/metadata?versionComment=METADATA_UPDATE&obeyMds=false"

        # Normalize metadata values for repository API
        normalized = normalize_for_repo(metadata, repo_field_ids)

        # Handle license transformation
        transform_license(normalized, metadata)

        # No default license on purpose: the extraction returns null when it
        # finds none, and filling that in would turn "we do not know" into a
        # claim that the material is free of copyright — published to a public
        # repository. An empty field is what hands the decision to the editorial
        # workflow this upload already enters.
        if "ccm:commonlicense_key" not in normalized:
            logger.info("📜 No license detected — left empty for editorial review")

        # Extract geo coordinates from schema:location → cm:latitude / cm:longitude
        extract_geo_coordinates(normalized, metadata)

        # Transform cm:author → ccm:lifecyclecontributer_author (VCARD format)
        transform_author_to_vcard(normalized)

        # Transform oeh:new_lrt → ccm:oeh_lrt (the property the repository keeps)
        transform_lrt(normalized, metadata)

        if not normalized:
            return {
                "success": True,
                "fields_written": 0,
                "fields_skipped": 0,
                "field_errors": [],
            }

        fields_to_write = set(normalized.keys())
        logger.info(f"📝 Writing {len(fields_to_write)} fields to node {node_id}")

        # --- Strategy 1: Bulk update (all fields at once) ---
        response = await client.post(
            metadata_url,
            headers={
                "Authorization": self._auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=normalized,
        )

        if response.status_code in (200, 201):
            logger.info(f"✅ Bulk metadata update succeeded: {len(normalized)} fields")
            return {
                "success": True,
                "fields_written": len(normalized),
                "fields_skipped": 0,
                "field_errors": [],
            }

        # --- Strategy 2: Bulk failed → field-by-field fallback ---
        bulk_error = response.text[:500]
        logger.warning(
            f"⚠️ Bulk metadata update failed ({response.status_code}): {bulk_error}"
        )
        logger.info("🔄 Retrying field-by-field to identify problematic fields...")

        fields_written = 0
        fields_skipped = 0
        field_errors = []

        for field_id, field_value in normalized.items():
            try:
                single_response = await client.post(
                    metadata_url,
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json={field_id: field_value},
                )

                if single_response.status_code in (200, 201):
                    fields_written += 1
                else:
                    fields_skipped += 1
                    error_text = single_response.text[:200]
                    field_errors.append(
                        {
                            "field_id": field_id,
                            "error": f"HTTP {single_response.status_code}: {error_text}",
                            "status_code": single_response.status_code,
                        }
                    )
                    logger.error(f"   ❌ {field_id}: {single_response.status_code}")
            except Exception as e:
                fields_skipped += 1
                field_errors.append(
                    {"field_id": field_id, "error": str(e), "status_code": None}
                )
                logger.error(f"   ❌ {field_id}: {e}")

        logger.info(
            f"📊 Field-by-field result: {fields_written} written, {fields_skipped} failed"
        )

        return {
            "success": fields_written > 0,
            "fields_written": fields_written,
            "fields_skipped": fields_skipped,
            "field_errors": field_errors,
        }

    async def _write_extended_fields(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        node_id: str,
        metadata: dict[str, Any],
        context: str,
        version: str,
        extended_text: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Write the 3 extended metadata fields to a node (bypasses repo_field filter).

        Fields:
        - ccm:oeh_extendedType: URI of the content type from core.json vocabulary
        - ccm:oeh_extendedData: Full metadata JSON after generation/extraction
        - ccm:oeh_extendedText: Raw source text before extraction
        """
        metadata_url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/metadata?versionComment=EXTENDED_DATA&obeyMds=false"

        extended_fields: dict[str, list[str]] = {}

        # 1. ccm:oeh_extendedType — resolve URI from metadataset (schema_file)
        type_uri = None
        schema_file = metadata.get("metadataset")
        if schema_file:
            type_uri = get_content_type_uri(schema_file, context, version)
            if type_uri:
                extended_fields["ccm:oeh_extendedType"] = [type_uri]
                logger.info(f"📎 extendedType: {type_uri} (from {schema_file})")
            else:
                logger.warning(
                    f"⚠️ extendedType: No URI found for schema_file={schema_file}"
                )

        # 1b. ccm:oeh_lrt — derived from the content type, but only as a fallback.
        # This write happens after the metadata write, so setting it here
        # unconditionally would replace whatever the extraction found. The
        # derivation knows six coarse types ('Material'); the extraction picks
        # from 220 ('Arbeitsblatt', 'Simulation'). The precise value wins.
        extracted_lrt = metadata.get("oeh:new_lrt") or metadata.get("ccm:oeh_lrt")
        if extracted_lrt:
            logger.info(
                f"📎 lrt: kept from extraction, not overwritten ({extracted_lrt})"
            )
        elif type_uri and type_uri in EXTENDED_TYPE_TO_NEW_LRT:
            lrt_uri = EXTENDED_TYPE_TO_NEW_LRT[type_uri]
            extended_fields["ccm:oeh_lrt"] = [lrt_uri]
            logger.info(
                f"📎 lrt: {lrt_uri} (from extendedType {type_uri.split('/')[-1]})"
            )

        # 2. ccm:oeh_extendedData — full metadata as JSON string
        # Backstop. upload_metadata and save_node refuse such a value before
        # they touch the repository, so reaching this is a bug — but writing it
        # would fix the Elasticsearch path to `text` and lock every later record
        # out of the index, which is far worse than a failed write. Raising here
        # lands in the caller's except, which discards the half-written node.
        if schema_file:
            violations = find_object_shape_violations(
                metadata, context, version, schema_file
            )
            if violations:
                raise ValueError(
                    "ccm:oeh_extendedData nicht geschrieben — "
                    + "; ".join(v["message"] for v in violations)
                )

        # Remove internal processing keys, keep only actual metadata fields
        # Handle nested format (web component export) vs flat format (API response)
        if "metadata" in metadata and isinstance(metadata.get("metadata"), dict):
            data_dict = {
                k: v for k, v in metadata["metadata"].items() if not k.startswith("_")
            }
        else:
            excluded_keys = {
                "contextName",
                "schemaVersion",
                "metadataset",
                "metadataset_uri",
                "language",
                "exportedAt",
                "processing",
                "_origins",
                "_source_text",
                "preview_image_url",
            }
            data_dict = {k: v for k, v in metadata.items() if k not in excluded_keys}
        if data_dict:
            extended_fields["ccm:oeh_extendedData"] = [
                json.dumps(data_dict, ensure_ascii=False)
            ]
            logger.info(
                f"📎 extendedData: {len(json.dumps(data_dict, ensure_ascii=False))} chars"
            )

        # 3. ccm:oeh_extendedText — raw source text
        if extended_text:
            extended_fields["ccm:oeh_extendedText"] = [extended_text]
            logger.info(f"📎 extendedText: {len(extended_text)} chars")

        if not extended_fields:
            logger.warning("⚠️ No extended fields to write")
            return {"success": True, "fields_written": 0}

        # Write all extended fields in one request
        try:
            response = await client.post(
                metadata_url,
                headers={
                    "Authorization": self._auth_header,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=extended_fields,
            )

            if response.status_code in (200, 201):
                logger.info(
                    f"✅ Extended fields written: {list(extended_fields.keys())}"
                )
                return {"success": True, "fields_written": len(extended_fields)}
            else:
                error_text = response.text[:300]
                logger.warning(
                    f"⚠️ Extended fields bulk write failed ({response.status_code}): {error_text}"
                )

                # Fallback: write field-by-field
                written = 0
                for field_id, field_value in extended_fields.items():
                    try:
                        single_resp = await client.post(
                            metadata_url,
                            headers={
                                "Authorization": self._auth_header,
                                "Content-Type": "application/json",
                                "Accept": "application/json",
                            },
                            json={field_id: field_value},
                        )
                        if single_resp.status_code in (200, 201):
                            written += 1
                            logger.info(f"   ✅ {field_id}")
                        else:
                            logger.error(f"   ❌ {field_id}: {single_resp.status_code}")
                    except Exception as e:
                        logger.error(f"   ❌ {field_id}: {e}")

                return {"success": written > 0, "fields_written": written}

        except Exception as e:
            logger.error(f"❌ Extended fields write failed: {e}")
            return {"success": False, "fields_written": 0, "error": str(e)}

    async def _ensure_aspects(
        self, client: httpx.AsyncClient, base_url: str, node_id: str, metadata: dict
    ) -> Optional[str]:
        """
        Add required aspects to node based on metadata content.

        Aspects are Alfresco extension packages that enable specific property groups.
        Without the correct aspect, the repo silently drops writes to those properties.

        Returns a message for the caller when an aspect could not be set, since
        the fields depending on it are then lost although the write reports them.
        """
        extra_aspects = []

        # cm:geographic → needed for cm:latitude, cm:longitude
        has_geo = False
        locations = metadata.get("schema:location")
        if locations:
            if isinstance(locations, list):
                for loc in locations:
                    if isinstance(loc, dict) and isinstance(loc.get("geo"), dict):
                        has_geo = True
                        break
            elif isinstance(locations, dict) and isinstance(locations.get("geo"), dict):
                has_geo = True
        # Also check schema:geo (organization.json top-level)
        if not has_geo and isinstance(metadata.get("schema:geo"), dict):
            geo = metadata["schema:geo"]
            if geo.get("latitude") is not None and geo.get("longitude") is not None:
                has_geo = True
        if has_geo:
            extra_aspects.append("cm:geographic")

        # cm:author → needed for ccm:lifecyclecontributer_author
        if metadata.get("cm:author"):
            extra_aspects.append("cm:author")

        if not extra_aspects:
            return None

        lost = (
            f"Aspekt(e) {', '.join(extra_aspects)} nicht gesetzt — "
            "Koordinaten bzw. Autor werden vom Repository verworfen"
        )

        # Read current aspects, merge, PUT back
        try:
            aspects_url = f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/aspects"
            r = await client.get(
                f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/metadata?propertyFilter=-all-",
                headers={
                    "Authorization": self._auth_header,
                    "Accept": "application/json",
                },
            )
            current_aspects = []
            if r.status_code == 200:
                current_aspects = r.json().get("node", {}).get("aspects", [])

            new_aspects = [a for a in extra_aspects if a not in current_aspects]
            if new_aspects:
                full_list = current_aspects + new_aspects
                r = await client.put(
                    aspects_url,
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=full_list,
                )
                if r.status_code == 200:
                    logger.info(f"🔧 Aspects added: {new_aspects}")
                else:
                    logger.warning(
                        f"⚠️ Failed to add aspects {new_aspects}: {r.status_code}"
                    )
                    return f"{lost} (HTTP {r.status_code})"
        except Exception as e:
            logger.warning(f"⚠️ Aspect update error: {e}")
            return lost
        return None

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
        config = _get_repository_config()

        base_url = config["base_url"]

        try:
            timeout = httpx.Timeout(30.0, connect=10.0)
            async with httpx.AsyncClient(
                timeout=timeout, event_hooks=TRACE_EVENT_HOOKS
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
                    return {
                        "success": False,
                        "error": f"Failed to fetch node: HTTP {response.status_code} — {response.text[:300]}",
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

        except httpx.TimeoutException as e:
            return {"success": False, "error": f"Timeout: {e}"}
        except Exception as e:
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

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
        its full history (which records who set which state).
        """
        config = _get_repository_config()
        base_url = config["base_url"]

        try:
            timeout = httpx.Timeout(45.0, connect=10.0)
            async with httpx.AsyncClient(
                timeout=timeout, event_hooks=TRACE_EVENT_HOOKS
            ) as client:
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

        except httpx.TimeoutException as e:
            return {
                "success": False,
                "nodeId": node_id,
                "error": f"Timeout bei der Verbindung zum Repository: {e}",
            }
        except httpx.ConnectError as e:
            return {
                "success": False,
                "nodeId": node_id,
                "error": f"Verbindung zum Repository fehlgeschlagen: {e}",
            }
        except Exception as e:
            return {
                "success": False,
                "nodeId": node_id,
                "error": f"{type(e).__name__}: {e}",
            }


# Singleton instance
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
