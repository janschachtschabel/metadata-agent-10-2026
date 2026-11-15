"""Models of /validate and /export/markdown."""

from typing import Any

from pydantic import BaseModel, Field

from .common import (
    MAX_CONTEXT_LENGTH,
    MAX_LANGUAGE_LENGTH,
    MAX_SCHEMA_FILE_LENGTH,
    MAX_VERSION_LENGTH,
    SchemaMarkersMixin,
)


class ValidateRequest(SchemaMarkersMixin):
    """
    Request model for metadata validation.

    All parameters default to 'auto' and are automatically detected from the metadata:
    - contextName → context
    - schemaVersion → version
    - metadataset → schema_file

    You can override any parameter by providing an explicit value.
    """

    # The metadata to validate - can be full export or just fields
    metadata: dict[str, Any] = Field(
        ..., description="Metadata JSON to validate (full export or nested)"
    )

    # Parameters with auto-detection (set to 'auto' to read from metadata)
    context: str = Field(
        max_length=MAX_CONTEXT_LENGTH,
        default="auto",
        description="Schema context ('auto' = read from contextName in metadata)",
    )
    version: str = Field(
        max_length=MAX_VERSION_LENGTH,
        default="auto",
        description="Schema version ('auto' = read from schemaVersion in metadata)",
    )
    schema_file: str = Field(
        max_length=MAX_SCHEMA_FILE_LENGTH,
        default="auto",
        description="Schema file ('auto' = read from metadataset in metadata)",
    )

    def get_effective_params(self) -> tuple[str, str, str, dict[str, Any]]:
        """
        Extract effective context, version, schema_file and clean metadata.
        Returns: (context, version, schema_file, clean_metadata)
        """
        metadata = self.metadata.copy()

        # Extract values from metadata
        meta_context = metadata.pop("contextName", None)
        meta_version = metadata.pop("schemaVersion", None)
        meta_schema = metadata.pop("metadataset", None)

        # Remove other meta fields
        metadata.pop("language", None)
        metadata.pop("exportedAt", None)
        metadata.pop("processing", None)

        # Also check old nested _schema format
        if "_schema" in metadata:
            # null is "no legacy block" (audit 14.11., L-37).
            schema_info = metadata.pop("_schema") or {}
            meta_context = meta_context or schema_info.get("context")
            meta_version = meta_version or schema_info.get("version")
            meta_schema = meta_schema or schema_info.get("file")

        # Use explicit value if not 'auto', otherwise use detected value, otherwise fallback
        context = meta_context if self.context == "auto" else self.context
        version = meta_version if self.version == "auto" else self.version
        schema_file = meta_schema if self.schema_file == "auto" else self.schema_file

        # Final fallbacks if still None
        context = context or "default"
        version = version or "latest"
        schema_file = schema_file or "auto"

        return context, version, schema_file, metadata


class ValidationError(BaseModel):
    """Single validation error."""

    field_id: str
    message: str
    severity: str = Field(description="error, warning, or info")


class ValidateResponse(BaseModel):
    """Response model for metadata validation."""

    valid: bool
    schema_used: str
    errors: list[ValidationError] = Field(default_factory=list)
    warnings: list[ValidationError] = Field(default_factory=list)
    coverage: float = Field(description="Percentage of required fields filled")


class ExportMarkdownRequest(SchemaMarkersMixin):
    """
    Request model for markdown export.

    Like ValidateRequest, all parameters default to 'auto' for auto-detection.
    Simply pass the complete output from /generate directly as `metadata`.
    """

    metadata: dict[str, Any] = Field(
        ..., description="Complete output from /generate endpoint - paste directly"
    )
    context: str = Field(
        max_length=MAX_CONTEXT_LENGTH,
        default="auto",
        description="Schema context ('auto' = read from metadata)",
    )
    version: str = Field(
        max_length=MAX_VERSION_LENGTH,
        default="auto",
        description="Schema version ('auto' = read from metadata)",
    )
    schema_file: str = Field(
        max_length=MAX_SCHEMA_FILE_LENGTH,
        default="auto",
        description="Schema file ('auto' = read from metadata)",
    )
    language: str = Field(
        max_length=MAX_LANGUAGE_LENGTH,
        default="auto",
        description="Output language ('auto' = read from metadata, fallback: de)",
    )
    include_empty: bool = Field(default=False, description="Include empty fields")

    schema_markers = (*SchemaMarkersMixin.schema_markers, "language")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "metadata": {
                        "contextName": "default",
                        "schemaVersion": "1.8.1",
                        "metadataset": "event.json",
                        "language": "de",
                        "cclom:title": "Workshop KI in der Bildung",
                        "cclom:general_description": "Ein Workshop über KI...",
                        "schema:actor": [{"name": "Max Mustermann"}],
                        "schema:location": [
                            {"name": "Berlin", "address": {"addressLocality": "Berlin"}}
                        ],
                    }
                }
            ]
        }
    }

    def get_effective_params(self) -> tuple[str, str, str, str, dict[str, Any]]:
        """
        Extract effective parameters and clean metadata.
        Returns: (context, version, schema_file, language, clean_metadata)
        """
        metadata = self.metadata.copy()

        # Extract values from metadata
        meta_context = metadata.pop("contextName", None)
        meta_version = metadata.pop("schemaVersion", None)
        meta_schema = metadata.pop("metadataset", None)
        meta_language = metadata.pop("language", None)

        # Remove other meta fields
        metadata.pop("exportedAt", None)
        metadata.pop("processing", None)

        # Also check old nested _schema format
        if "_schema" in metadata:
            # null is "no legacy block" (audit 14.11., L-37).
            schema_info = metadata.pop("_schema") or {}
            meta_context = meta_context or schema_info.get("context")
            meta_version = meta_version or schema_info.get("version")
            meta_schema = meta_schema or schema_info.get("file")

        # Use explicit value if not 'auto', otherwise use detected value
        context = meta_context if self.context == "auto" else self.context
        version = meta_version if self.version == "auto" else self.version
        schema_file = meta_schema if self.schema_file == "auto" else self.schema_file
        language = meta_language if self.language == "auto" else self.language

        # Final fallbacks
        context = context or "default"
        version = version or "latest"
        schema_file = schema_file or "auto"
        language = language or "de"

        return context, version, schema_file, language, metadata


class ExportMarkdownResponse(BaseModel):
    """Response model for markdown export."""

    markdown: str
    schema_used: str
