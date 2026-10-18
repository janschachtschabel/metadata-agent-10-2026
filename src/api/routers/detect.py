"""Content type detection (/detect-content-type)."""

import asyncio
import time
from fastapi import HTTPException

from ...models.schemas import (
    DetectContentTypeRequest,
    DetectContentTypeResponse,
    ContentTypeInfo,
    LocalizedString,
)
from ...services.input_source_service import TextExtractionError
from ...services.llm_errors import LLMError
from ...utils.schema_loader import (
    get_content_types,
    get_latest_version,
)


from fastapi import APIRouter

from .. import deps
from ..input_resolver import resolve_input, truncate
from ..responses import (
    generation_timeout_response,
    llm_error_response,
    text_extraction_error_response,
)


router = APIRouter()


# ============================================================================
# Content Type Detection
# ============================================================================


@router.post(
    "/detect-content-type",
    response_model=DetectContentTypeResponse,
    summary="Content-Type erkennen",
    description="""Analysiert Text und erkennt automatisch den passenden Content-Type (Schema) für die Metadaten-Extraktion.

## Input-Quellen (input_source)

| Wert | Beschreibung | Benötigte Felder |
|------|-------------|------------------|
| `text` | Direkter Text als Eingabe (Standard) | `text` |
| `url` | Text von URL via Crawler abrufen | `source_url`, optional `extraction_method` |
| `node_id` | Text + Metadaten von Repository-Node abrufen | `node_id`, `repository` |
| `node_url` | Repository-Node + Crawler-Fallback (URL aus ccm:wwwurl) | `node_id`, `repository` (source_url optional) |

## Extraction Method (extraction_method)

| Wert | Beschreibung |
|------|-------------|
| `simple` | Schnelle HTML-Extraktion |
| `browser` | Vollständiges Browser-Rendering für JS-Seiten (Standard) |

## Output Format (output_format)

| Wert | Beschreibung |
|------|-------------|
| `markdown` | Markdown-Format (Standard) |
| `txt` | Reiner Text |
| `html` | Rohes HTML |

## Repository

> **Deprecated:** Der Parameter `repository` wird ignoriert. Die Repository-URL wird über `METADATA_AGENT_REPOSITORY_URL` konfiguriert. Der Parameter wird aus Abwärtskompatibilität akzeptiert.

## LLM-Optionen

- **llm_provider**: `b-api-openai` (Standard), `b-api-academiccloud`, `b-api-router`, `openai`
- **llm_model**: Ohne Angabe gilt der Default des Providers — `gpt-6-luna` (b-api-openai), `deepseek-v4-flash` (b-api-academiccloud), `openai/gpt-6-luna` (b-api-router, Routenname oder provider/modell), `gpt-4o-mini` (openai)
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/DetectContentTypeRequest"},
                    "examples": {
                        "text_input": {
                            "summary": "1. Text-Eingabe",
                            "description": "Direkter Text als Eingabe.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.\nLernen Sie die Grundlagen der künstlichen Intelligenz kennen.",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "context": "default",
                                "version": "latest",
                                "language": "de",
                            },
                        },
                        "url_input": {
                            "summary": "2. URL-Eingabe",
                            "description": "Text von URL abrufen.",
                            "value": {
                                "input_source": "url",
                                "text": "",
                                "source_url": "https://www.wirlernenonline.de",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "context": "default",
                                "version": "latest",
                                "language": "de",
                            },
                        },
                        "node_id_input": {
                            "summary": "3. NodeID-Eingabe",
                            "description": "Text von Repository-Node abrufen.",
                            "value": {
                                "input_source": "node_id",
                                "text": "",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
                                "context": "default",
                                "version": "latest",
                                "language": "de",
                            },
                        },
                    },
                }
            }
        }
    },
)
async def detect_content_type(req: DetectContentTypeRequest):
    """
    Detect content type from text using LLM.

    Returns the detected content type along with all available content types
    for the specified context and version. Fetching the source and the AI call
    together end at METADATA_AGENT_GENERATE_TIMEOUT (audit P-01).
    """
    try:
        async with asyncio.timeout(deps.settings.generate_timeout):
            return await _detect(req)
    except TimeoutError:
        return generation_timeout_response(deps.settings.generate_timeout)


async def _detect(req: DetectContentTypeRequest):
    start_time = time.time()

    # Cheap refusals before the source is fetched (audit API-05).
    version = req.version
    if version == "latest" or not version:
        version = get_latest_version(req.context)
    content_types = get_content_types(req.context, version)
    if not content_types:
        raise HTTPException(
            status_code=404,
            detail=f"No content types defined for context '{req.context}' version '{version}'",
        )
    deps.check_provider_override(req.llm_provider)

    try:
        resolved = await resolve_input(req)
    except TextExtractionError as e:
        return text_extraction_error_response(e)
    text = resolved.text
    input_warnings = resolved.warnings

    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    text = truncate(text)

    # Build available list
    available = []
    for ct in content_types:
        schema_file = ct.get("schema_file", "")
        available.append(
            ContentTypeInfo(
                schema_file=schema_file,
                uri=ct.get("uri") or None,
                profile_id=ct.get("profile_id"),
                label=LocalizedString(
                    de=ct.get("label", {}).get("de", schema_file),
                    en=ct.get("label", {}).get("en", schema_file),
                ),
                confidence=None,
            )
        )

    # Use LLM to detect content type (with optional overrides). A fallback is
    # labelled as one; a refused key is an error, never "learning material".
    service = deps.get_metadata_service(
        llm_provider=req.llm_provider, llm_model=req.llm_model
    )
    try:
        detection = await service.detect_schema(
            text, req.context, version, req.language
        )
    except LLMError as e:
        return llm_error_response(e)
    finally:
        # Close non-default LLM service HTTP client to prevent leak
        if req.llm_provider is not None or req.llm_model is not None:
            await service.llm_service.close()
    detected_schema = detection["schema_file"]

    # Find the detected content type info
    detected_info = None
    for ct in content_types:
        if ct.get("schema_file") == detected_schema:
            detected_info = ContentTypeInfo(
                schema_file=detected_schema,
                uri=ct.get("uri") or None,
                profile_id=ct.get("profile_id"),
                label=LocalizedString(
                    de=ct.get("label", {}).get("de", detected_schema),
                    en=ct.get("label", {}).get("en", detected_schema),
                ),
                confidence=detection["confidence"],
            )
            break

    if not detected_info:
        # Fallback if detection returned unknown schema
        detected_info = ContentTypeInfo(
            schema_file=detected_schema,
            uri=None,
            profile_id=None,
            label=LocalizedString(de=detected_schema, en=detected_schema),
            confidence="low",
        )

    processing_time = int((time.time() - start_time) * 1000)

    return DetectContentTypeResponse(
        detected=detected_info,
        available=available,
        context=req.context,
        version=version,
        processing_time_ms=processing_time,
        method=detection["method"],
        warnings=input_warnings
        + ([detection["warning"]] if detection.get("warning") else []),
    )
