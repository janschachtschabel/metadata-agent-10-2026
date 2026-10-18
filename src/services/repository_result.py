"""The result of one write (/upload, /nodes), typed, and the answer built from it."""

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
