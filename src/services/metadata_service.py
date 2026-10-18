"""
Metadata extraction and processing service.

MetadataService runs a generation (content type, fields, LLM, merge,
normalisation, geocoding, order). The steps that need no service live in
metadata_generation, validation in metadata_validation, the Markdown export in
metadata_export.
"""

import time
from datetime import datetime, timezone
from typing import Any, Optional

from ..config import get_settings
from ..utils.schema_loader import (
    get_content_types,
    get_content_type_prompt,
    detect_schema_from_text,
)
from . import metadata_export, metadata_validation
from .llm_auth import current_auth_source
from .llm_errors import LLMError
from .llm_service import get_llm_service
from .geocoding_service import get_geocoding_service
from .metadata_generation import (
    UPDATE_MARKER_WARNING,
    apply_update_markers,
    final_origins,
    generation_status,
    get_default_value_for_field,
    load_extraction_fields,
    log_re_extraction,
    merge_extracted,
    order_by_schema,
    parse_update_text,
    run_outcome,
    select_fields_to_extract,
)
from .output_normalizer import get_output_normalizer
from .field_normalizer import get_field_normalizer
import logging

logger = logging.getLogger(__name__)

# Importable from here as in 2.1.1.
__all__ = [
    "MetadataService",
    "generation_status",
    "get_default_value_for_field",
    "get_metadata_service",
    "parse_update_text",
]


class MetadataService:
    """Service for metadata extraction orchestration."""

    def __init__(
        self, llm_provider: Optional[str] = None, llm_model: Optional[str] = None
    ):
        """
        Initialize metadata service.

        Args:
            llm_provider: Override default LLM provider
            llm_model: Override default LLM model
        """
        self.llm_service = get_llm_service(
            llm_provider=llm_provider, llm_model=llm_model
        )
        self.geocoding_service = get_geocoding_service()
        self.output_normalizer = get_output_normalizer()
        self.field_normalizer = get_field_normalizer()
        self.settings = get_settings()

    async def generate_metadata(
        self,
        text: str,
        context: str = "default",
        version: str = "latest",
        schema_file: str = "auto",
        existing_metadata: Optional[dict[str, Any]] = None,
        language: str = "de",
        max_workers: int = 20,
        include_core: bool = True,
        enable_geocoding: bool = True,
        normalize_output: bool = True,
        normalize_vocabularies: bool = True,
        regenerate_fields: Optional[list[str]] = None,
        regenerate_empty: bool = False,
        origins: Optional[dict[str, str]] = None,
        deadline: Optional[float] = None,
    ) -> dict[str, Any]:
        """
        Generate metadata from text.

        Args:
            text: Input text to extract metadata from
            context: Schema context
            version: Schema version
            schema_file: Schema file or 'auto' for detection
            existing_metadata: Existing metadata to update
            language: Extraction language
            max_workers: Parallel worker count
            include_core: Include core fields (title, description, etc.)
            enable_geocoding: Convert location addresses to coordinates
            normalize_output: Apply normalization to extracted values
            normalize_vocabularies: Normalize vocabulary values using fuzzy matching
            regenerate_fields: List of field IDs to regenerate (re-extract)
            regenerate_empty: Re-extract fields that are empty in existing_metadata
            origins: Existing field origins dict (field_id -> 'ai'|'user')
            deadline: Loop time the whole run must end by (the /generate
                deadline); geocoding gets only what is left of it

        Returns:
            Generated metadata with statistics
        """
        start_time = time.time()
        errors: list[str] = []
        warnings: list[str] = []

        text, existing_metadata, from_markers = apply_update_markers(
            text, existing_metadata
        )
        if from_markers:
            warnings.append(UPDATE_MARKER_WARNING)

        def failed(schema_used, message, error=None):
            return self._build_error_response(
                context,
                version,
                schema_used,
                language,
                int((time.time() - start_time) * 1000),
                self.llm_service.provider,
                self.llm_service.model,
                message,
                error=error,
            )

        if schema_file == "auto":
            try:
                detection = await self.detect_schema(text, context, version, language)
            except LLMError as e:
                # The same credentials would fail every field: stop before the
                # field calls instead of sending dozens of refused requests.
                return failed(None, e.message, e)
            schema_file = detection["schema_file"]
            if detection.get("warning"):
                warnings.append(detection["warning"])
            content_type_detection = {
                k: v for k, v in detection.items() if k != "warning"
            }
        else:
            content_type_detection = {
                "method": "preselected",
                "schema_file": schema_file,
            }

        try:
            all_fields = load_extraction_fields(
                context, version, schema_file, include_core, warnings
            )
        except Exception as e:
            return failed(schema_file, f"Schema loading failed: {str(e)}")

        extraction = await self.llm_service.extract_fields_parallel(
            fields=select_fields_to_extract(
                all_fields,
                existing_metadata,
                regenerate_fields,
                regenerate_empty,
                origins,
            ),
            text=text,
            existing_metadata=existing_metadata,
            language=language,
            max_workers=max_workers,
        )
        extracted_values = extraction.get("values", {})
        errors.extend(extraction.get("errors", []))
        if extraction.get("fatal_error") is not None:
            errors.insert(0, extraction["fatal_error"].message)
        if existing_metadata:
            log_re_extraction(extracted_values)

        flat_metadata, field_origins = merge_extracted(
            existing_metadata, extracted_values, origins, regenerate_fields
        )
        field_schemas: dict[Any, dict] = {
            f.get("id"): f.get("system", {}) for f in all_fields
        }
        flat_metadata = await self._post_process(
            flat_metadata,
            field_schemas,
            language,
            normalize_output,
            normalize_vocabularies,
            enable_geocoding,
            warnings,
            deadline,
        )
        ordered_metadata = order_by_schema(
            flat_metadata, context, version, schema_file, include_core
        )

        outcome = run_outcome(extraction, extracted_values)
        processing = {
            "status": outcome["status"],
            "success": outcome["status"] != "failed",
            "fields_extracted": outcome["fields_extracted"],
            "fields_failed": outcome["fields_failed"],
            "fields_total": len(all_fields),
            "processing_time_ms": int((time.time() - start_time) * 1000),
            "llm_provider": self.llm_service.provider,
            "llm_model": self.llm_service.model,
            "auth_source": self._auth_source(),
            "content_type_detection": content_type_detection,
            "errors": errors,
            "warnings": warnings,
        }
        if outcome["error"] is not None:
            processing["error"] = outcome["error"]

        return {
            "contextName": context,
            "schemaVersion": version,
            "metadataset": schema_file,
            "language": language,
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "metadata": ordered_metadata,
            "_origins": final_origins(
                ordered_metadata, field_origins, extracted_values
            ),
            "processing": processing,
        }

    async def _post_process(
        self,
        flat_metadata: dict[str, Any],
        field_schemas: dict[Any, dict],
        language: str,
        normalize_output: bool,
        normalize_vocabularies: bool,
        enable_geocoding: bool,
        warnings: list[str],
        deadline: Optional[float] = None,
    ) -> dict[str, Any]:
        """
        Normalise values by their schema, geocode locations, shape the output.

        Each step that fails leaves the metadata as it was and adds a warning.
        """
        if normalize_output:
            for field_id, value in flat_metadata.items():
                if value is not None and field_id in field_schemas:
                    try:
                        flat_metadata[field_id] = (
                            self.field_normalizer.normalize_field_value(
                                value, field_schemas[field_id], normalize_vocabularies
                            )
                        )
                    except Exception as e:
                        warnings.append(
                            f"Field normalization warning for {field_id}: {str(e)}"
                        )

        # After all extraction: coordinates for schema:location
        if enable_geocoding:
            try:
                flat_metadata = (
                    await self.geocoding_service.enrich_metadata_with_geocoding(
                        flat_metadata, language, deadline=deadline, warnings=warnings
                    )
                )
            except Exception as e:
                warnings.append(f"Geocoding failed: {str(e)}")

        # Structure as the web component expects it
        if normalize_output:
            try:
                flat_metadata = self.output_normalizer.normalize_output(flat_metadata)
            except Exception as e:
                warnings.append(f"Output normalization warning: {str(e)}")

        return flat_metadata

    def _auth_source(self) -> Optional[str]:
        """Which key the B-API calls of this request carry - never the key itself."""
        if not self.llm_service.requires_custom_header:
            return "openai"
        return current_auth_source(self.settings)

    async def detect_schema(
        self, text: str, context: str, version: str, language: str = "de"
    ) -> dict[str, Any]:
        """
        Determine the content type, openly labelled when it is only a fallback.

        Returns {"schema_file", "method", "confidence"} plus "reason" and
        "warning" for a fallback. method is "llm" when the model named one of
        the offered types, "keywords" when the keyword detection had to stand in.

        Raises:
            LLMError: a fatal LLM failure - no fallback can make up for a
                refused key, and the field extraction would fail the same way.
        """
        content_types = get_content_types(context, version)
        if content_types:
            prompt_hint = get_content_type_prompt(context, version, language)
            detection = await self.llm_service.detect_content_type(
                text, content_types, language, prompt_hint=prompt_hint
            )
            if detection.schema_file:
                return {
                    "method": "llm",
                    "confidence": "high",
                    "schema_file": detection.schema_file,
                }
            reason = detection.problem
        else:
            reason = "für diesen Kontext ist keine KI-Erkennung konfiguriert"

        fallback = detect_schema_from_text(text, context, version)
        return {
            "method": "keywords",
            "confidence": "low",
            "schema_file": fallback,
            "reason": reason,
            "warning": (
                f"Inhaltsart nicht per KI erkannt ({reason}); per Schlagwortsuche als "
                f"{fallback} eingeordnet – bitte prüfen."
            ),
        }

    def _build_error_response(
        self,
        context: str,
        version: str,
        schema_file: str,
        language: str,
        processing_time: int,
        llm_provider: str,
        llm_model: str,
        error_message: str,
        error: Optional[LLMError] = None,
    ) -> dict[str, Any]:
        """Build error response in the new format."""
        return {
            "contextName": context,
            "schemaVersion": version,
            "metadataset": schema_file,
            "language": language,
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "metadata": {},
            "processing": {
                "status": "failed",
                "success": False,
                "fields_extracted": 0,
                # The one failure that ended the run, counted like a fatal
                # error during extraction (generate_metadata).
                "fields_failed": 1,
                "fields_total": 0,
                "processing_time_ms": processing_time,
                "llm_provider": llm_provider,
                "llm_model": llm_model,
                "auth_source": self._auth_source(),
                "errors": [error_message],
                "warnings": [],
                "error": (
                    error.to_dict()
                    if error is not None
                    else {"code": "GENERATION_FAILED", "message": error_message}
                ),
            },
        }

    def validate_metadata(
        self,
        metadata: dict[str, Any],
        context: str = "default",
        version: str = "latest",
        schema_file: str = "auto",
    ) -> dict[str, Any]:
        """Validate metadata against its schema (metadata_validation.validate_metadata)."""
        return metadata_validation.validate_metadata(
            metadata, self.field_normalizer, context, version, schema_file
        )

    def export_to_markdown(
        self,
        metadata: dict[str, Any],
        context: str = "default",
        version: str = "latest",
        schema_file: str = "auto",
        language: str = "de",
        include_empty: bool = False,
    ) -> str:
        """Metadata as Markdown (metadata_export.export_to_markdown)."""
        return metadata_export.export_to_markdown(
            metadata, context, version, schema_file, language, include_empty
        )


# Singleton instance (default config)
_metadata_service: Optional[MetadataService] = None


def get_metadata_service(
    llm_provider: Optional[str] = None, llm_model: Optional[str] = None
) -> MetadataService:
    """
    Get metadata service instance.

    If LLM overrides are specified, creates a new instance.
    Otherwise returns the cached default instance.
    """
    global _metadata_service

    # If overrides specified, create new instance with those settings
    if llm_provider is not None or llm_model is not None:
        return MetadataService(llm_provider=llm_provider, llm_model=llm_model)

    # Return cached default instance
    if _metadata_service is None:
        _metadata_service = MetadataService()
    return _metadata_service
