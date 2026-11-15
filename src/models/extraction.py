"""
Models of /generate, /detect-content-type and /extract-field: where the text
comes from and what the model run answers.
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import MAX_WORKERS, LLMProvider
from .common import (
    LLM_MODEL_MAX_LENGTH,
    LLM_MODEL_PATTERN,
    MAX_CONTEXT_LENGTH,
    MAX_FIELD_ID_LENGTH,
    MAX_LANGUAGE_LENGTH,
    MAX_SCHEMA_FILE_LENGTH,
    MAX_URL_LENGTH,
    MAX_VERSION_LENGTH,
    LocalizedString,
    blank_is_unset,
    normalize_provider,
    sanitize_text,
    validate_node_id,
)
from .screenshot import ScreenshotMethod


class InputSource(str, Enum):
    """Input source for metadata generation."""

    TEXT = "text"  # Direct text input (default)
    URL = "url"  # Fetch text via text extraction API
    NODE_ID = "node_id"  # Fetch from repository by NodeID
    NODE_URL = (
        "node_url"  # Use NodeID + URL (prefers stored data, falls back to crawler)
    )


class ExtractionMethod(str, Enum):
    """Text extraction method for URL input."""

    SIMPLE = "simple"
    BROWSER = "browser"


class OutputFormat(str, Enum):
    """Output format for text extraction."""

    MARKDOWN = "markdown"
    TXT = "txt"
    HTML = "html"


class InputSourceRequest(BaseModel):
    """
    Where the text comes from: what /generate, /detect-content-type and
    /extract-field have in common. Their own fields follow this block.
    """

    # Input source selection
    input_source: InputSource = Field(
        default=InputSource.TEXT,
        description="Input source: 'text' (direct input), 'url' (fetch via crawler), 'node_id' (fetch from repository), 'node_url' (repository + crawler fallback)",
    )

    # Text input (required for input_source='text')
    text: Optional[str] = Field(
        default=None,
        description="Input text to extract metadata from. Required when input_source='text'.",
    )

    # URL input (required for input_source='url' or 'node_url')
    source_url: Optional[str] = Field(
        default=None,
        max_length=MAX_URL_LENGTH,
        description="URL to fetch text from via text extraction API. Required when input_source='url'; with 'node_url' it is optional (default: ccm:wwwurl of the node).",
    )
    extraction_method: ExtractionMethod = Field(
        default=ExtractionMethod.BROWSER,
        description="Text extraction method: 'simple' (fast, basic HTML parsing) or 'browser' (full browser rendering, slower but more complete)",
    )
    output_format: OutputFormat = Field(
        default=OutputFormat.MARKDOWN,
        description="Output format for text extraction: 'markdown' (default), 'txt' (plain text), 'html' (raw HTML)",
    )

    # NodeID input (required for input_source='node_id' or 'node_url')
    node_id: Optional[str] = Field(
        default=None,
        description="Repository NodeID to fetch metadata and text from. Required when input_source='node_id' or 'node_url'. Must be a node UUID.",
    )
    _check_node_id = field_validator("node_id")(validate_node_id)
    repository: str = Field(
        default="staging",
        deprecated=True,
        description="Deprecated — wird ignoriert. Die Repository-URL kommt aus METADATA_AGENT_REPOSITORY_URL. Wird aus Abwärtskompatibilität weiterhin akzeptiert.",
    )

    @field_validator("text", mode="before")
    @classmethod
    def sanitize_text_input(cls, v: Any) -> str:
        """Sanitize text input before validation."""
        if isinstance(v, str):
            return sanitize_text(v)
        return v

    # Its own name: a subclass that reused it would replace this validator.
    _blank_input_is_unset = field_validator("text", "source_url", mode="before")(
        blank_is_unset
    )


class GenerateRequest(InputSourceRequest):
    """Request model for metadata generation."""

    # Common options
    existing_metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Existing metadata JSON to use as base (will be updated/enriched). For node_id/node_url sources, fetched metadata is merged.",
    )
    context: str = Field(
        max_length=MAX_CONTEXT_LENGTH,
        default="default",
        description="Schema context to use (e.g., 'default', 'mds_oeh')",
    )
    version: str = Field(
        max_length=MAX_VERSION_LENGTH,
        default="latest",
        description="Schema version to use ('latest' for newest version, or specific like '1.8.1')",
    )
    schema_file: str = Field(
        max_length=MAX_SCHEMA_FILE_LENGTH,
        default="auto",
        description="Schema file to use ('auto' for automatic detection, specific filename like 'event.json', or a vocab URI like 'http://w3id.org/openeduhub/vocabs/contentTypes/event')",
    )
    language: str = Field(
        max_length=MAX_LANGUAGE_LENGTH,
        default="de",
        description="Primary language for extraction (de/en)",
    )
    max_workers: int = Field(
        default=MAX_WORKERS,
        ge=1,
        le=MAX_WORKERS,
        description=f"Maximum number of parallel LLM workers (1-{MAX_WORKERS}). Without it /generate uses METADATA_AGENT_DEFAULT_MAX_WORKERS (default {MAX_WORKERS}).",
    )
    include_core: bool = Field(
        default=True,
        description="Include core fields (title, description, keywords, etc.) in extraction",
    )
    enable_geocoding: bool = Field(
        default=True,
        description="Enable geocoding to convert location addresses to coordinates (uses Photon API)",
    )

    # Normalization option
    normalize: bool = Field(
        default=True,
        description="Apply normalization to extracted values (dates, booleans, vocabularies, etc.)",
    )

    # Field regeneration options
    regenerate_fields: Optional[list[str]] = Field(
        default=None,
        description="List of field IDs to regenerate (re-extract from text). Other fields use existing_metadata.",
    )
    regenerate_empty: bool = Field(
        default=False,
        description="Re-extract fields that are empty/null in existing_metadata",
    )

    # LLM options (override defaults from .env)
    llm_provider: Optional[LLMProvider] = Field(
        default=None,
        description="LLM provider to use. Options: 'openai' (native OpenAI API), 'b-api-openai' (OpenAI via B-API, default), 'b-api-academiccloud' (DeepSeek via B-API), 'b-api-router' (B-API router: a route name or provider/model). If not set, uses METADATA_AGENT_LLM_PROVIDER from environment.",
    )
    llm_model: Optional[str] = Field(
        default=None,
        max_length=LLM_MODEL_MAX_LENGTH,
        pattern=LLM_MODEL_PATTERN,
        description="LLM model to use. Without it the provider's configured default applies: 'gpt-6-luna' (b-api-openai), 'deepseek-v4-flash' (b-api-academiccloud), 'openai/gpt-6-luna' (b-api-router; works without a route), 'gpt-4o-mini' (openai).",
    )

    # Screenshot options (async preview generation during extraction)
    preview_url: Optional[str] = Field(
        default=None,
        max_length=MAX_URL_LENGTH,
        description="URL to capture as preview screenshot (runs async parallel to KI extraction). If not set, auto-detected from source_url or ccm:wwwurl.",
    )
    # The enum of /screenshot and /upload: as free text every non-empty value,
    # "none" and "off" included, started a capture (audit 14.11., S-40).
    screenshot_method: Optional[ScreenshotMethod] = Field(
        default=None,
        description="Screenshot method: 'playwright' (local) or the legacy alias 'pageshot' (also captured locally, no external service). If not set, no screenshot is captured.",
    )

    @field_validator("existing_metadata")
    @classmethod
    def _internal_keys_have_their_shape(
        cls, value: Optional[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        """
        `_origins` and `_source_text`, as the web component sends them.

        Of another kind they ended as a 500 in the middle of the run (audit
        13.11., L-19).
        """
        if not value:
            return value
        origins = value.get("_origins")
        if origins is not None and not (
            isinstance(origins, dict)
            and all(isinstance(origin, str) for origin in origins.values())
        ):
            raise ValueError(
                "_origins muss ein Objekt aus Feld-ID und Herkunft (ai, user) sein"
            )
        source_text = value.get("_source_text")
        if source_text is not None and not isinstance(source_text, str):
            raise ValueError("_source_text muss ein Text sein")
        return value

    _blank_is_unset = field_validator(
        "llm_provider",
        "llm_model",
        "preview_url",
        "screenshot_method",
        mode="before",
    )(blank_is_unset)
    _normalize_provider = field_validator("llm_provider", mode="before")(
        normalize_provider
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin. Lernen Sie die Grundlagen der künstlichen Intelligenz kennen.",
                    "existing_metadata": {"cclom:title": "Mein Workshop"},
                    "context": "default",
                    "version": "latest",
                    "schema_file": "auto",
                    "language": "de",
                    "include_core": True,
                    "screenshot_method": "playwright",
                    "preview_url": "",
                }
            ]
        }
    }


class ProcessingInfo(BaseModel):
    """Processing statistics and debug info (`processing` of /generate)."""

    model_config = ConfigDict(extra="allow")

    status: str = Field(
        description="success, partial (some fields failed) or failed (no value at all)"
    )
    success: bool = Field(description="status is not 'failed'")
    fields_extracted: int = Field(description="Number of fields with values")
    fields_failed: int = Field(
        default=0, description="Fields that failed for a technical reason"
    )
    fields_total: int = Field(description="Total number of fields in schema")
    processing_time_ms: int = Field(description="Processing time in milliseconds")
    llm_provider: str = Field(description="LLM provider used")
    llm_model: str = Field(description="LLM model used")
    auth_source: Optional[str] = Field(
        default=None, description="Which key the LLM calls carried (never the key)"
    )
    content_type_detection: Optional[dict[str, Any]] = Field(
        default=None,
        description="How the content type was chosen: method (preselected, llm, "
        "keywords), schema_file, confidence, reason",
    )
    error: Optional[dict[str, Any]] = Field(
        default=None,
        description="The cause of a partial or failed run: code, message and "
        "further details",
    )
    errors: list[str] = Field(
        default_factory=list, description="Any errors encountered"
    )
    warnings: list[str] = Field(default_factory=list, description="Any warnings")


class GenerateResponse(BaseModel):
    """
    Response of /generate (documentation only: the endpoint sends this shape as is).

    Header, then every non-empty metadata field at top level under its field id
    (additional properties), then `_origins`, `_source_text`,
    `preview_image_url` where present, and `processing` last (audit API-09).
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # Meta information (header)
    contextName: str = Field(description="Schema context name")
    schemaVersion: str = Field(description="Schema version used")
    metadataset: str = Field(description="Schema file that was used")
    metadataset_uri: Optional[str] = Field(
        default=None,
        description="Vocab URI for the metadataset (e.g., 'http://w3id.org/openeduhub/vocabs/contentTypes/event')",
    )
    language: str = Field(description="Language used for extraction")
    exportedAt: str = Field(description="ISO timestamp of generation")

    origins: Optional[dict[str, str]] = Field(
        default=None,
        alias="_origins",
        description="Origin of every returned field: 'ai' or 'user'",
    )
    source_text: Optional[str] = Field(
        default=None,
        alias="_source_text",
        description="The text the values were extracted from - send it back in "
        "existing_metadata for a correction round",
    )
    preview_image_url: Optional[str] = Field(
        default=None, description="Screenshot as data URL, when one was taken"
    )

    processing: ProcessingInfo = Field(
        description="Processing statistics and debug info"
    )


class DetectContentTypeRequest(InputSourceRequest):
    """Request model for content type detection."""

    # The shared block, with this endpoint's wording for the text.
    text: Optional[str] = Field(
        default=None,
        description="Input text to analyze. Required when input_source='text'.",
    )

    # Detection options
    context: str = Field(
        max_length=MAX_CONTEXT_LENGTH,
        default="default",
        description="Schema context to use",
    )
    version: str = Field(
        max_length=MAX_VERSION_LENGTH,
        default="latest",
        description="Schema version to use ('latest' for newest)",
    )
    language: str = Field(
        max_length=MAX_LANGUAGE_LENGTH,
        default="de",
        description="Language for detection (de/en)",
    )

    # LLM options (override defaults from .env)
    llm_provider: Optional[LLMProvider] = Field(
        default=None,
        description="LLM provider to use. Options: 'openai' (native OpenAI API), 'b-api-openai' (OpenAI via B-API, default), 'b-api-academiccloud' (DeepSeek via B-API), 'b-api-router' (B-API router: a route name or provider/model). If not set, uses METADATA_AGENT_LLM_PROVIDER from environment.",
    )
    llm_model: Optional[str] = Field(
        default=None,
        max_length=LLM_MODEL_MAX_LENGTH,
        pattern=LLM_MODEL_PATTERN,
        description="LLM model to use. Without it the provider's configured default applies: 'gpt-6-luna' (b-api-openai), 'deepseek-v4-flash' (b-api-academiccloud), 'openai/gpt-6-luna' (b-api-router; works without a route), 'gpt-4o-mini' (openai).",
    )

    _blank_is_unset = field_validator("llm_provider", "llm_model", mode="before")(
        blank_is_unset
    )
    _normalize_provider = field_validator("llm_provider", mode="before")(
        normalize_provider
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "input_source": "text",
                    "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                    "source_url": "",
                    "extraction_method": "browser",
                    "node_id": "",
                    "context": "default",
                    "version": "latest",
                    "language": "de",
                }
            ]
        }
    }


class ContentTypeInfo(BaseModel):
    """Information about a detected content type."""

    schema_file: str = Field(description="Schema file name (e.g., 'event.json')")
    uri: Optional[str] = Field(
        default=None,
        description="Vocab URI for this content type (e.g., 'http://w3id.org/openeduhub/vocabs/contentTypes/event')",
    )
    profile_id: Optional[str] = Field(
        default=None, description="Profile ID if available"
    )
    label: LocalizedString = Field(description="Localized label for the content type")
    confidence: Optional[str] = Field(
        default=None, description="Detection confidence (high/medium/low)"
    )


class DetectContentTypeResponse(BaseModel):
    """Response model for content type detection."""

    detected: ContentTypeInfo = Field(description="Detected content type")
    available: list[ContentTypeInfo] = Field(
        description="All available content types for this context/version"
    )
    context: str = Field(description="Schema context used")
    version: str = Field(description="Schema version used")
    processing_time_ms: int = Field(description="Processing time in milliseconds")
    method: str = Field(
        default="llm",
        description=(
            "How the content type was determined: 'llm' (the model named one of the "
            "offered types, confidence 'high') or 'keywords' (the model gave no usable "
            "answer and the keyword detection stood in, confidence 'low' - check it)"
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Notes about the detection, e.g. why the keyword fallback was used",
    )


class ExtractFieldRequest(InputSourceRequest):
    """Request model for single field extraction."""

    # The shared block, with this endpoint's wording for the text.
    text: Optional[str] = Field(
        default=None,
        description="Input text to extract the field value from. Required when input_source='text'.",
    )

    # Field-specific options
    context: str = Field(
        max_length=MAX_CONTEXT_LENGTH,
        default="default",
        description="Schema context to use",
    )
    version: str = Field(
        max_length=MAX_VERSION_LENGTH,
        default="latest",
        description="Schema version to use ('latest' for newest)",
    )
    schema_file: str = Field(
        ...,
        max_length=MAX_SCHEMA_FILE_LENGTH,
        description="Schema file containing the field (e.g., 'event.json', 'core.json') or a vocab URI (e.g., 'http://w3id.org/openeduhub/vocabs/contentTypes/event')",
    )
    field_id: str = Field(
        ...,
        max_length=MAX_FIELD_ID_LENGTH,
        description="Field ID to extract (e.g., 'ccm:oeh_event_begin', 'cclom:title')",
    )
    existing_metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Existing metadata JSON with current field values (for context in update scenarios). For node_id/node_url sources, fetched metadata is merged.",
    )
    language: str = Field(
        max_length=MAX_LANGUAGE_LENGTH,
        default="de",
        description="Language for extraction (de/en)",
    )
    normalize: bool = Field(
        default=True,
        description="Apply normalization to extracted value (dates, vocabularies, etc.)",
    )

    # LLM options (override defaults from .env)
    llm_provider: Optional[LLMProvider] = Field(
        default=None,
        description="LLM provider to use. Options: 'openai' (native OpenAI API), 'b-api-openai' (OpenAI via B-API, default), 'b-api-academiccloud' (DeepSeek via B-API), 'b-api-router' (B-API router: a route name or provider/model). If not set, uses METADATA_AGENT_LLM_PROVIDER from environment.",
    )
    llm_model: Optional[str] = Field(
        default=None,
        max_length=LLM_MODEL_MAX_LENGTH,
        pattern=LLM_MODEL_PATTERN,
        description="LLM model to use. Without it the provider's configured default applies: 'gpt-6-luna' (b-api-openai), 'deepseek-v4-flash' (b-api-academiccloud), 'openai/gpt-6-luna' (b-api-router; works without a route), 'gpt-4o-mini' (openai).",
    )

    _blank_is_unset = field_validator("llm_provider", "llm_model", mode="before")(
        blank_is_unset
    )
    _normalize_provider = field_validator("llm_provider", mode="before")(
        normalize_provider
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "context": "default",
                    "version": "latest",
                    "schema_file": "event.json",
                    "field_id": "ccm:oeh_event_begin",
                    "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                    "language": "de",
                    "normalize": True,
                },
                {
                    "context": "default",
                    "version": "latest",
                    "schema_file": "event.json",
                    "field_id": "ccm:oeh_event_begin",
                    "text": "Der Workshop wurde auf den 20. März 2025 verschoben.",
                    "existing_metadata": {"ccm:oeh_event_begin": "2025-03-15T00:00"},
                    "language": "de",
                    "normalize": True,
                },
            ]
        }
    }


class ExtractFieldResponse(BaseModel):
    """Response model for single field extraction."""

    field_id: str = Field(description="Field ID that was extracted")
    field_label: Optional[str] = Field(
        default=None, description="Human-readable field label"
    )
    value: Any = Field(description="Extracted (and normalized) value")
    raw_value: Optional[Any] = Field(
        default=None, description="Value before normalization (if different)"
    )
    previous_value: Optional[Any] = Field(
        default=None, description="Previous value if provided"
    )
    changed: bool = Field(description="Whether the value changed from previous")
    normalized: bool = Field(description="Whether normalization was applied")
    context: str = Field(description="Schema context used")
    version: str = Field(description="Schema version used")
    schema_file: str = Field(description="Schema file used")
    processing: dict[str, Any] = Field(
        description="Processing info (provider, model, time)"
    )
