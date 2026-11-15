"""
The result of one write (/upload, /nodes): the dicts RepositoryService answers
with, the same typed, and the response built from it.
"""

from dataclasses import dataclass, fields
from typing import Any, Optional, TypeVar

from ..models.schemas import (
    CollectionResult,
    FieldUploadError,
    SaveNodeResponse,
    UploadedNodeInfo,
    UploadResponse,
    WorkflowStepResult,
)
from .repository_support import identifying_url, text_value
from .repository_write import StepResults, WriteProgress, WriteTarget

# error_code of a write that left nothing behind (audit 18.10. final, API-08).
# A partial result - the node exists with what was written - carries none.
METADATA_INVALID = "metadata_invalid"
SCHEMA_UNAVAILABLE = "schema_unavailable"
WRITE_FAILED = "write_failed"

# HTTP status and API error code for each of them, as the routers answer.
_NOTHING_WRITTEN = {
    METADATA_INVALID: (422, "VALIDATION_ERROR"),
    # Unknown contextName/schemaVersion: the caller's to fix.
    SCHEMA_UNAVAILABLE: (422, "SCHEMA_UNAVAILABLE"),
    WRITE_FAILED: (502, "UPSTREAM_ERROR"),
}

WriteResponse = TypeVar("WriteResponse", UploadResponse, SaveNodeResponse)


@dataclass(frozen=True)
class WriteResult:
    """RepositoryService's answer for /upload and /nodes, read once."""

    success: bool = False
    error: Optional[str] = None
    error_code: Optional[str] = None
    duplicate: Optional[bool] = None
    node: Optional[dict[str, Any]] = None
    node_full: Optional[dict[str, Any]] = None
    # A node this call created and moved to the recycle bin again.
    discarded_node: Optional[str] = None
    step: Optional[str] = None
    fields_written: Optional[int] = None
    fields_skipped: Optional[int] = None
    # The fields counted in fields_skipped that no property was written for.
    skipped_fields: Optional[list[str]] = None
    node_created: Optional[bool] = None
    schema_used: Optional[str] = None
    repo_fields_available: Optional[int] = None
    field_errors: Optional[list[dict[str, Any]]] = None
    preview: Optional[dict[str, Any]] = None
    collections: Optional[list[dict[str, Any]]] = None
    workflow: Optional[list[dict[str, Any]]] = None

    @classmethod
    def from_service(cls, result: dict[str, Any]) -> "WriteResult":
        """The known keys of the service dict; others (violations) stay out, as before."""
        known = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in result.items() if key in known})

    @property
    def nothing_written(self) -> Optional[tuple[int, str]]:
        """(HTTP status, error code) when the write left nothing behind, else None."""
        return _NOTHING_WRITTEN.get(self.error_code or "")

    def present(self, *keys: str) -> dict[str, Any]:
        """The named entries that hold a value, for an error answer's extra keys."""
        return {key: getattr(self, key) for key in keys if getattr(self, key)}

    def response(
        self, model: type[WriteResponse], preview: Optional[dict[str, Any]]
    ) -> WriteResponse:
        """
        The 200 answer of /upload or /nodes; the one mapping both share.

        `preview` is passed in: /upload reports how its screenshot was taken.
        """
        answer: dict[str, Any] = {
            "success": self.success,
            "duplicate": self.duplicate,
            "node": UploadedNodeInfo(**self.node) if self.node else None,
            "node_full": self.node_full,
            "error": self.error,
            "step": self.step,
            "fields_written": self.fields_written,
            "fields_skipped": self.fields_skipped,
            "skipped_fields": self.skipped_fields,
            "node_created": self.node_created,
            "schema_used": self.schema_used,
            "repo_fields_available": self.repo_fields_available,
            "field_errors": [FieldUploadError(**e) for e in self.field_errors]
            if self.field_errors
            else None,
            "preview": preview,
            "collections": [CollectionResult(**c) for c in self.collections]
            if self.collections is not None
            else None,
            "workflow": [WorkflowStepResult(**s) for s in self.workflow]
            if self.workflow is not None
            else None,
            "discarded_node": self.discarded_node,
        }
        # /nodes has no duplicate check and so no `duplicate` key.
        return model(**{k: v for k, v in answer.items() if k in model.model_fields})


# ------------------------------------------------- the service's result dicts


def no_writable_fields_result(
    context: str, version: str, schema_file: Optional[str]
) -> dict:
    """
    The answer when the schemas name no field that may be written.

    That only happens when the schema could not be loaded (unknown context or
    version, unreadable file). Writing anyway would create a node holding just
    its title and report it as a success.
    """
    schemas = f"core.json + {schema_file}" if schema_file else "core.json"
    return {
        "success": False,
        "error_code": SCHEMA_UNAVAILABLE,
        "step": "schema",
        "error": (
            f"Für Kontext '{context}', Version '{version}' ({schemas}) ist kein "
            "Repository-Feld bekannt — das Schema ließ sich nicht laden. Es wurde "
            "nichts geschrieben."
        ),
    }


def unloadable_schema_result(context: str, version: str, schema_file: Any) -> dict:
    """
    The answer for a `metadataset` whose schema cannot be loaded (audit 14.11., R-04).

    A typo, a file the version does not have or a URI naming no content type
    used to fall back to core.json: the type's fields were dropped and the
    answer still said success.
    """
    # Caller's text: one line, bounded.
    named = " ".join(str(schema_file).split())[:120]
    return {
        "success": False,
        "error_code": SCHEMA_UNAVAILABLE,
        "step": "schema",
        "error": (
            f"Das Schema '{named}' (metadataset) ließ sich für Kontext "
            f"'{context}', Version '{version}' nicht laden — bitte Dateinamen "
            "bzw. Inhaltstyp-URI und Schema-Version prüfen. Es wurde nichts "
            "geschrieben."
        ),
    }


def created_result(
    base_url: str, node_id: str, clean_metadata: dict[str, Any]
) -> dict[str, Any]:
    """The answer of POST /node for a node that exists now."""
    return {
        "success": True,
        "node": {
            "nodeId": node_id,
            "title": text_value(clean_metadata.get("cclom:title")),
            "wwwurl": identifying_url(clean_metadata),
            "repositoryUrl": f"{base_url}/components/render/{node_id}",
        },
    }


def duplicate_result(
    base_url: str, duplicate: dict[str, Any], clean_metadata: dict[str, Any]
) -> dict[str, Any]:
    """The refusal for a URL the repository already holds, with that node."""
    existing = duplicate.get("nodeId")
    title = duplicate.get("title")
    named = f'"{title}"' if title else f"Node {existing}" if existing else ""
    result: dict[str, Any] = {
        "success": False,
        "duplicate": True,
        "error": f"URL existiert bereits: {named}".rstrip(": "),
    }
    if existing:
        result["node"] = {
            "nodeId": existing,
            "title": title,
            "description": duplicate.get("description"),
            "wwwurl": identifying_url(clean_metadata),
            "repositoryUrl": f"{base_url}/components/render/{existing}",
        }
    return result


def written_result(
    base_url: str,
    node_id: str,
    target: WriteTarget,
    progress: WriteProgress,
    metadata_result: dict[str, Any],
    steps: StepResults,
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
        "skipped_fields": metadata_result.get("skipped_fields", []),
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
