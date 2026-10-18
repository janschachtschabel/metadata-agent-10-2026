"""Single-field extraction (/extract-field)."""

import logging
import asyncio
import time
from fastapi import HTTPException

from ...models.schemas import (
    ExtractFieldRequest,
    ExtractFieldResponse,
)
from ...services.input_source_service import TextExtractionError
from ...services.llm_errors import LLMError
from ...services.field_normalizer import get_field_normalizer
from ...utils.schema_loader import (
    load_schema,
    get_latest_version,
    resolve_schema_file_or_uri,
)


from fastapi import APIRouter

from .. import deps
from ..input_resolver import resolve_input, truncate
from ..responses import (
    generation_timeout_response,
    llm_error_response,
    text_extraction_error_response,
)

logger = logging.getLogger(__name__)


router = APIRouter()


# ============================================================================
# Single Field Extraction
# ============================================================================


@router.post(
    "/extract-field",
    response_model=ExtractFieldResponse,
    summary="Einzelnes Feld extrahieren",
    description="""Extrahiert oder regeneriert einen einzelnen Feldwert mittels LLM. Nützlich um einzelne Felder zu korrigieren ohne alles neu zu extrahieren.

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

> **Deprecated:** Der Parameter `repository` wird ignoriert. Die Repository-URL wird über `METADATA_AGENT_REPOSITORY_URL` konfiguriert.

## Feld-Optionen

- **schema_file**: Schema-Datei die das Feld enthält, z.B. `event.json`, `core.json`
- **field_id**: Feld-ID zum Extrahieren, z.B. `ccm:oeh_event_begin`, `cclom:title`
- **existing_metadata**: Bestehende Metadaten als Kontext (optional)
- **normalize**: Normalisierung anwenden (Standard: `true`)

## LLM-Optionen

- **llm_provider**: `b-api-openai` (Standard), `b-api-academiccloud`, `b-api-router`, `openai`
- **llm_model**: Ohne Angabe gilt der Default des Providers — `gpt-6-luna` (b-api-openai), `deepseek-v4-flash` (b-api-academiccloud), `openai/gpt-6-luna` (b-api-router, Routenname oder provider/modell), `gpt-4o-mini` (openai)
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ExtractFieldRequest"},
                    "examples": {
                        "text_input": {
                            "summary": "1. Text-Eingabe",
                            "description": "Extrahiert ein Feld aus direktem Text.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "field_id": "ccm:oeh_event_begin",
                                "existing_metadata": {},
                                "language": "de",
                                "normalize": True,
                            },
                        },
                        "url_input": {
                            "summary": "2. URL-Eingabe",
                            "description": "Extrahiert ein Feld aus Text einer URL.",
                            "value": {
                                "input_source": "url",
                                "text": "",
                                "source_url": "https://www.wirlernenonline.de",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "field_id": "cclom:title",
                                "existing_metadata": {},
                                "language": "de",
                                "normalize": True,
                            },
                        },
                        "node_id_input": {
                            "summary": "3. NodeID-Eingabe",
                            "description": "Extrahiert ein Feld aus Repository-Node Daten.",
                            "value": {
                                "input_source": "node_id",
                                "text": "",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "field_id": "ccm:oeh_event_begin",
                                "existing_metadata": {},
                                "language": "de",
                                "normalize": True,
                            },
                        },
                        "feld_korrigieren": {
                            "summary": "4. Feld korrigieren",
                            "description": "Korrigiert ein Feld basierend auf existing_metadata.",
                            "value": {
                                "input_source": "text",
                                "text": "Der Workshop wurde auf den 20. März 2025 verschoben.",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "field_id": "ccm:oeh_event_begin",
                                "existing_metadata": {
                                    "ccm:oeh_event_begin": "2025-03-15T00:00"
                                },
                                "language": "de",
                                "normalize": True,
                            },
                        },
                    },
                }
            }
        }
    },
)
async def extract_field(req: ExtractFieldRequest):
    """
    Extract a single field from text.

    Use this endpoint to:
    - Fix individual fields without full re-extraction
    - Test different LLM models on specific fields
    - Update a single field with new information

    The extraction uses the same LLM pipeline as /generate but for one field only.
    Normalization is applied by default (dates, vocabularies, etc.). Fetching
    the source and the AI call together end at METADATA_AGENT_GENERATE_TIMEOUT.
    """
    try:
        async with asyncio.timeout(deps.settings.generate_timeout):
            return await _extract(req)
    except TimeoutError:
        return generation_timeout_response(deps.settings.generate_timeout)


async def _extract(req: ExtractFieldRequest):
    start_time = time.time()

    # An unknown schema or field and a provider override the deployment does
    # not allow are refused before the source is fetched (audit API-05).
    version, resolved_schema_file, field_def = _field_definition(req)
    deps.check_provider_override(req.llm_provider)

    try:
        resolved = await resolve_input(req)
    except TextExtractionError as e:
        return text_extraction_error_response(e)
    text = resolved.text
    input_warnings = resolved.warnings
    # Values the caller sends win over those the node already has.
    existing_metadata = {**resolved.fetched_metadata, **(req.existing_metadata or {})}

    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    text = truncate(text)

    # Get LLM service with optional overrides
    llm_service = deps.get_llm_service(
        llm_provider=req.llm_provider, llm_model=req.llm_model
    )

    existing_value = existing_metadata.get(req.field_id) if existing_metadata else None

    # Extract the field (returns tuple: field_id, value, error)
    try:
        _, raw_value, error = await llm_service.extract_field(
            field=field_def,
            text=text,
            existing_value=existing_value,
            language=req.language,
        )
    except LLMError as e:
        return llm_error_response(e)
    finally:
        # Close non-default LLM service HTTP client to prevent leak
        if req.llm_provider is not None or req.llm_model is not None:
            await llm_service.close()

    if isinstance(error, LLMError):
        return llm_error_response(error)
    if error:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {error}")

    value = raw_value
    if req.normalize and raw_value is not None:
        normalized_value = get_field_normalizer().normalize_field_value(
            value=raw_value,
            field_schema=field_def.get("system", field_def),
            normalize_vocabularies=True,
        )
        # An equal value (3 for 3.0) keeps what the LLM returned.
        if normalized_value != raw_value:
            value = normalized_value
    normalized = value is not raw_value

    field_label = field_def.get("label", {})
    if isinstance(field_label, dict):
        field_label = field_label.get(req.language, field_label.get("de", req.field_id))

    processing_time = int((time.time() - start_time) * 1000)

    return ExtractFieldResponse(
        field_id=req.field_id,
        field_label=field_label,
        value=value,
        raw_value=raw_value if normalized else None,
        previous_value=existing_value,
        changed=value != existing_value,
        normalized=normalized,
        context=req.context,
        version=version,
        schema_file=resolved_schema_file,
        processing={
            "llm_provider": llm_service.provider,
            "llm_model": llm_service.model,
            "processing_time_ms": processing_time,
            "warnings": input_warnings,
        },
    )


def _field_definition(req: ExtractFieldRequest) -> tuple[str, str, dict]:
    """
    (version, schema file, field definition) for the requested field.

    'latest' and schema URIs are resolved; a field the content type does not
    declare is looked up in core.json. 400 for an unknown schema URI, 404 for
    a schema or field that does not exist.
    """
    version = req.version
    if version == "latest" or not version:
        version = get_latest_version(req.context)

    try:
        schema_file = resolve_schema_file_or_uri(req.schema_file, req.context, version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        schema = load_schema(req.context, version, schema_file)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    field_def = _find_field(schema, req.field_id)
    if not field_def:
        try:
            field_def = _find_field(
                load_schema(req.context, version, "core.json"), req.field_id
            )
        except ValueError as e:
            # The field is then reported as unknown; why core.json is missing
            # belongs in the log (audit L-04).
            logger.warning(f"⚠️ core.json of {req.context}/{version} not loaded: {e}")
    if not field_def:
        raise HTTPException(
            status_code=404,
            detail=f"Field '{req.field_id}' not found in schema '{schema_file}' or core.json",
        )
    return version, schema_file, field_def


def _find_field(schema: dict, field_id: str):
    return next((f for f in schema.get("fields", []) if f.get("id") == field_id), None)
