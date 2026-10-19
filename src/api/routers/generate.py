"""Full metadata generation (/generate)."""

import asyncio
import base64
import json
from typing import Any, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from ...models.schemas import (
    GenerateRequest,
    GenerateResponse,
    InputSource,
)
from ...services.input_source_service import TextExtractionError
from ...metrics import GENERATIONS
from ...services.screenshot_service import (
    LOCAL_METHODS,
)
from ...utils.loggable import loggable_url
from ...utils.schema_loader import (
    get_latest_version,
    load_schema,
    resolve_schema_file_or_uri,
    get_content_type_uri,
)
import logging

from pydantic import ValidationError


from fastapi import APIRouter

from .. import deps
from ..deps import (
    sanitize_json_string,
)
from ...services.llm_prompts import correction_round
from ..input_resolver import (
    MAX_CORRECTION_LENGTH,
    resolve_input,
    truncate,
    truncation_warnings,
    with_correction,
)
from .request_body import invalid_body
from ..responses import (
    generation_failed_response,
    generation_timeout_response,
    text_extraction_error_response,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# Main Endpoints
# ============================================================================


def _timeout_response():
    return generation_timeout_response(deps.settings.generate_timeout)


@router.post(
    "/generate",
    response_model=GenerateResponse,
    summary="Metadaten generieren",
    description="""Generiert vollständige Metadaten aus Text, URL oder Repository-Node mittels LLM.

## Input-Quellen (input_source)

| Wert | Beschreibung | Benötigte Felder |
|------|-------------|------------------|
| `text` | Direkter Text als Eingabe (Standard) | `text` |
| `url` | Text von URL via Crawler abrufen | `source_url`, optional `extraction_method` |
| `node_id` | Metadaten + hinterlegte Volltexte von Repository-Node | `node_id`, `repository` |
| `node_url` | Repository-Node + Crawler-Fallback (URL aus ccm:wwwurl) | `node_id`, `repository` (source_url optional, wird aus Metadaten geholt) |

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

## Schema-Optionen

- **context**: Schema-Kontext, z.B. `default`, `mds_oeh`
- **version**: Schema-Version, `latest` (Standard) oder spezifisch z.B. `1.8.1`
- **schema_file**: `auto` (automatische Erkennung), oder spezifisch z.B. `event.json`

## Extraktions-Optionen

- **language**: Sprache für Extraktion (`de` oder `en`)
- **max_workers**: Parallele LLM-Worker (1-20). Ohne Angabe gilt `METADATA_AGENT_DEFAULT_MAX_WORKERS` (Standard 20); zusätzlich durch das Durchsatzlimit des Providers gedeckelt
- **include_core**: Core-Felder einbeziehen (Standard: `true`)
- **enable_geocoding**: Adressen zu Koordinaten umwandeln (Standard: `true`)
- **normalize**: Normalisierung für Datum, Boolean, Vokabulare (Standard: `true`)

## Regenerations-Optionen

- **existing_metadata**: Bestehende Metadaten als Basis (werden erweitert)
- **regenerate_fields**: Liste von Feld-IDs die neu extrahiert werden sollen
- **regenerate_empty**: Leere Felder in existing_metadata neu extrahieren (Standard: `false`)

## Screenshot-Optionen (Vorschaubild parallel zur Extraktion)

- **screenshot_method**: `playwright` (lokal) oder der Legacy-Alias `pageshot` (nimmt ebenfalls lokal auf, kein externer Dienst). Wenn nicht gesetzt: kein Screenshot.
- **preview_url**: URL für Screenshot (optional). Wenn nicht gesetzt: wird aus `source_url` oder bestehenden Metadaten (`ccm:wwwurl`) ermittelt.
- Antwort enthält `preview_image_url` als Base64 Data-URL wenn Screenshot erfolgreich.

## LLM-Optionen

- **llm_provider**: `b-api-openai` (Standard), `b-api-academiccloud`, `b-api-router`, `openai`
- **llm_model**: Ohne Angabe gilt der Default des Providers — `gpt-6-luna` (b-api-openai), `deepseek-v4-flash` (b-api-academiccloud), `openai/gpt-6-luna` (b-api-router, Routenname oder provider/modell), `gpt-4o-mini` (openai)
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/GenerateRequest"},
                    "examples": {
                        "text_input": {
                            "summary": "1. Text-Eingabe (Standard)",
                            "description": "Direkter Text als Eingabe. Schema wird automatisch erkannt.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.\nLernen Sie die Grundlagen der künstlichen Intelligenz kennen.",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "existing_metadata": {},
                                "context": "default",
                                "version": "latest",
                                "schema_file": "auto",
                                "language": "de",
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": [],
                                "regenerate_empty": False,
                                "screenshot_method": "",
                                "preview_url": "",
                            },
                        },
                        "url_input": {
                            "summary": "2. URL-Eingabe (Crawler)",
                            "description": "Text von URL abrufen via Text-Extraction-API. extraction_method: 'simple' (schnell) oder 'browser' (JS-Rendering). Mit screenshot_method wird parallel ein Vorschaubild erstellt.",
                            "value": {
                                "input_source": "url",
                                "text": "",
                                "source_url": "https://www.wirlernenonline.de",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "existing_metadata": {},
                                "context": "default",
                                "version": "latest",
                                "schema_file": "auto",
                                "language": "de",
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": [],
                                "regenerate_empty": False,
                                "screenshot_method": "playwright",
                                "preview_url": "",
                            },
                        },
                        "node_id_input": {
                            "summary": "3. NodeID-Eingabe (Repository)",
                            "description": "Metadaten + hinterlegte Volltexte von Repository-Node abrufen.",
                            "value": {
                                "input_source": "node_id",
                                "text": "",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
                                "existing_metadata": {},
                                "context": "default",
                                "version": "latest",
                                "schema_file": "auto",
                                "language": "de",
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": [],
                                "regenerate_empty": False,
                                "screenshot_method": "playwright",
                                "preview_url": "",
                            },
                        },
                        "node_url_input": {
                            "summary": "4. NodeID+URL (kombiniert)",
                            "description": "Repository-Metadaten + Volltext nutzen. Falls kein Volltext: URL aus ccm:wwwurl holen und Crawler nutzen.",
                            "value": {
                                "input_source": "node_url",
                                "text": "",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
                                "existing_metadata": {},
                                "context": "default",
                                "version": "latest",
                                "schema_file": "auto",
                                "language": "de",
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": [],
                                "regenerate_empty": False,
                                "screenshot_method": "playwright",
                                "preview_url": "",
                            },
                        },
                        "mit_existing_metadata": {
                            "summary": "5. Mit bestehenden Metadaten",
                            "description": "existing_metadata als Basis, nur bestimmte Felder neu extrahieren.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "existing_metadata": {
                                    "cclom:title": "Mein Workshop",
                                    "cclom:general_keyword": ["KI", "Bildung"],
                                },
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "language": "de",
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": ["ccm:oeh_event_begin"],
                                "regenerate_empty": True,
                                "screenshot_method": "",
                                "preview_url": "",
                            },
                        },
                        "anderer_provider": {
                            "summary": "6. Anderer LLM-Provider",
                            "description": "Provider und Modell nur für diese Anfrage. AcademicCloud verträgt 2 Requests gleichzeitig — mehr Worker warten nur.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                                "llm_provider": "b-api-academiccloud",
                                "llm_model": "deepseek-v4-flash",
                            },
                        },
                    },
                },
                "text/plain": {
                    "schema": {"type": "string"},
                    "examples": {
                        "mehrzeiliger_text": {
                            "summary": "Mehrzeiliger Text (ohne JSON)",
                            "description": "Für mehrzeilige Texte: Einfach den Text einfügen. Schema wird automatisch erkannt.",
                            "value": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.\n\nLernen Sie die Grundlagen der künstlichen Intelligenz kennen.\n\nZielgruppe: Lehrkräfte\nKosten: 49 Euro",
                        }
                    },
                },
            }
        }
    },
)
async def generate_metadata(request: Request):
    """
    Generate metadata from text.

    Extracts metadata from text using AI. Control characters are auto-sanitized.

    **Output Format:**
    - Header: contextName, schemaVersion, metadataset, language, exportedAt
    - metadata: Flat key-value pairs (field_id: value)
    - processing: Success status, stats, LLM info, errors/warnings
    """
    # One deadline for fetching the source and generating: together they must
    # stay below the ingress timeout (METADATA_AGENT_GENERATE_TIMEOUT).
    deadline = asyncio.get_running_loop().time() + deps.settings.generate_timeout

    req = await _parse_request(request)
    existing_metadata = req.existing_metadata or {}
    # Passed by the web component next to the values.
    origins = existing_metadata.pop("_origins", None) if existing_metadata else None
    original_source_text = (
        existing_metadata.pop("_source_text", None) if existing_metadata else None
    )

    if req.input_source == InputSource.TEXT and not (req.text and req.text.strip()):
        raise HTTPException(
            status_code=400, detail="Text is required when input_source='text'"
        )
    # Cheap refusals before the source is fetched (audit API-05).
    version, schema_file = _version_and_schema(req)
    deps.check_provider_override(req.llm_provider)
    request_warnings: list[str] = []
    if original_source_text and req.text and len(req.text) > MAX_CORRECTION_LENGTH:
        # The 2.1.1 widget sends _source_text with every run: a text this long
        # is a new source pasted over the old one, not a correction (final
        # audit 18.10.) - extracted as such instead of refused.
        original_source_text = None
        request_warnings.append(
            f"Text länger als {MAX_CORRECTION_LENGTH} Zeichen: als neue Quelle "
            "extrahiert, nicht als Korrekturanweisung zur bisherigen."
        )
    try:
        async with asyncio.timeout_at(deadline):
            resolved = await resolve_input(req)
    except TextExtractionError as e:
        return text_extraction_error_response(e)
    except TimeoutError:
        GENERATIONS.labels("failed").inc()
        return _timeout_response()
    # Values the caller sends win over those the node already has.
    existing_metadata = {**resolved.fetched_metadata, **existing_metadata}
    text, source_text, cut = _texts(
        req, resolved.text, original_source_text, existing_metadata
    )
    request_warnings += cut
    service = deps.get_metadata_service(
        llm_provider=req.llm_provider, llm_model=req.llm_model
    )
    # An override gets its own LLMService with its own HTTP client, which this
    # request has to close on every way out.
    own_llm_client = req.llm_provider is not None or req.llm_model is not None

    async def release():
        """Stop the capture nobody will await, close the request's own client."""
        if screenshot_task:
            screenshot_task.cancel()
        if own_llm_client:
            await service.llm_service.close()

    # Runs in parallel to the extraction.
    screenshot_task = _start_screenshot(req, existing_metadata)

    # The same condition _texts uses for building the correction text.
    is_correction = bool(original_source_text and req.text and req.text.strip())
    try:
        async with asyncio.timeout_at(deadline):
            with correction_round(is_correction):
                result = await service.generate_metadata(
                    text=text,
                    context=req.context,
                    version=version,
                    schema_file=schema_file,
                    existing_metadata=existing_metadata,
                    language=req.language,
                    max_workers=req.max_workers,
                    include_core=req.include_core,
                    enable_geocoding=req.enable_geocoding,
                    normalize_output=req.normalize,
                    normalize_vocabularies=req.normalize,
                    regenerate_fields=req.regenerate_fields,
                    regenerate_empty=req.regenerate_empty,
                    origins=origins,
                    deadline=deadline,
                )
    except TimeoutError:
        # Field tasks were cancelled with the run (asyncio.timeout).
        GENERATIONS.labels("failed").inc()
        await release()
        return _timeout_response()
    except BaseException as error:
        # A cancelled request (client gone) is not a failed generation. The
        # capture holds one of the few browser slots for up to MAX_CAPTURE_SECONDS.
        if isinstance(error, Exception):
            GENERATIONS.labels("failed").inc()
        await release()
        raise

    if request_warnings or resolved.warnings:
        processing = result.setdefault("processing", {})
        processing["warnings"] = (
            request_warnings + resolved.warnings + processing.get("warnings", [])
        )

    GENERATIONS.labels((result.get("processing") or {}).get("status", "unknown")).inc()

    if (result.get("processing") or {}).get("status") == "failed":
        # Not one usable field: answer with the error instead of an empty
        # "success" that a client would apply over existing metadata.
        await release()
        return generation_failed_response(result)

    try:
        preview_image_url = await _screenshot_data_url(screenshot_task, deadline)
    finally:
        # Close non-default LLM service HTTP client to prevent leak - also when
        # the wait is cancelled (CancelledError is no Exception).
        if own_llm_client:
            await service.llm_service.close()

    metadataset_uri = get_content_type_uri(result["metadataset"], req.context, version)
    return JSONResponse(
        content=_flat_response(result, metadataset_uri, source_text, preview_image_url)
    )


async def _parse_request(request: Request) -> GenerateRequest:
    """The request from a JSON body (sanitised) or a text/plain body with defaults."""
    content_type = request.headers.get("content-type", "application/json")

    try:
        body_str = (await request.body()).decode("utf-8")

        data: Any
        if "text/plain" in content_type:
            data = {
                "text": body_str,
                "context": "default",
                "version": "latest",
                "schema_file": "auto",
                "language": "de",
                "include_core": True,
            }
        else:
            try:
                data = json.loads(sanitize_json_string(body_str))
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid JSON: {str(e)}. Tip: Use Content-Type 'text/plain' for multi-line text input.",
                )
            # Same answer as parse_json_object; GenerateRequest(**data) would
            # say "argument after ** must be a mapping" with a 422.
            if not isinstance(data, dict):
                raise HTTPException(
                    status_code=400,
                    detail='Body muss ein JSON-Objekt sein, z.B. {"text": "..."}.',
                )

        # Without max_workers the ENV default applies (Settings keeps it
        # inside the range the request model accepts).
        if isinstance(data, dict) and "max_workers" not in data:
            data["max_workers"] = deps.settings.default_max_workers

        try:
            return GenerateRequest(**data)
        except ValidationError as e:
            raise invalid_body(e)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Request parsing error: {str(e)}")


def _texts(
    req: GenerateRequest,
    resolved_text: str,
    original_source_text: Optional[str],
    existing_metadata: dict[str, Any],
) -> tuple[str, str, list[str]]:
    """
    (text for the LLM, source text to return, warning if the source was cut).

    Re-extraction: the source, the values extracted so far and the correction
    instruction (req.text). The source is the fetched text, or with text input
    the source text of the previous round.
    """
    if not resolved_text or not resolved_text.strip():
        raise HTTPException(
            status_code=400, detail="No text content available from input source"
        )
    if original_source_text and req.text and req.text.strip():
        source = (
            original_source_text
            if req.input_source == InputSource.TEXT
            else resolved_text
        )
        text, kept = with_correction(source, existing_metadata, req.text)
        return text, kept, truncation_warnings(source, kept)
    text = truncate(resolved_text)
    return text, text, truncation_warnings(resolved_text, text)


def _version_and_schema(req: GenerateRequest) -> tuple[str, str]:
    """
    The concrete schema version ('latest' resolved) and schema file (URI resolved).

    400 for an unknown schema URI, 404 for a context, version or schema file
    that does not exist - like /extract-field, and before the source is fetched
    (audit API-06). With schema_file 'auto' the context's core.json stands in.
    """
    version = req.version
    if version == "latest" or not version:
        version = get_latest_version(req.context)

    schema_file = req.schema_file
    if schema_file and schema_file != "auto":
        try:
            schema_file = resolve_schema_file_or_uri(schema_file, req.context, version)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    try:
        load_schema(
            req.context,
            version,
            schema_file if schema_file and schema_file != "auto" else "core.json",
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return version, schema_file


def _start_screenshot(
    req: GenerateRequest, existing_metadata: dict[str, Any]
) -> Optional[asyncio.Task]:
    """
    Start the capture next to the extraction, or None.

    URL: explicit preview_url, else source_url, else ccm:wwwurl of the record.
    """
    if not req.screenshot_method:
        return None

    screenshot_url = req.preview_url or req.source_url
    if not screenshot_url and existing_metadata:
        screenshot_url = existing_metadata.get("ccm:wwwurl")
        if isinstance(screenshot_url, list):
            screenshot_url = screenshot_url[0] if screenshot_url else None
    if not screenshot_url:
        return None

    method = (
        req.screenshot_method
        if req.screenshot_method in LOCAL_METHODS
        else "playwright"
    )
    try:
        ss_service = deps.get_screenshot_service()
        logger.info(
            f"📸 Starting async screenshot in /generate: {loggable_url(screenshot_url)} "
            f"(method: {method})"
        )
        return asyncio.create_task(
            ss_service.capture(url=screenshot_url, method=method)
        )
    except Exception as e:
        logger.warning(f"⚠️ Screenshot task creation failed: {e}")
        return None


async def _screenshot_data_url(
    screenshot_task: Optional[asyncio.Task], deadline: float
) -> Optional[str]:
    """
    The capture as data URL for immediate display; None without one, on
    failure, or when it is not ready by the run's deadline - the metadata is
    there, the preview is not worth a 504 (audit P-01).
    """
    if not screenshot_task:
        return None
    try:
        async with asyncio.timeout_at(deadline):
            ss_result = await screenshot_task
    except TimeoutError:
        screenshot_task.cancel()
        logger.warning("⚠️ Screenshot not ready by the deadline; answering without it")
        return None
    except Exception as e:
        logger.warning(f"⚠️ Screenshot capture failed: {e}")
        return None
    if not (ss_result and ss_result.image_bytes):
        return None
    b64 = base64.b64encode(ss_result.image_bytes).decode("ascii")
    logger.info(
        f"📸 Screenshot captured: {len(ss_result.image_bytes)} bytes ({ss_result.capture_time_ms}ms)"
    )
    return f"data:image/{ss_result.format or 'png'};base64,{b64}"


def _flat_response(
    result: dict[str, Any],
    metadataset_uri: Optional[str],
    source_text: str,
    preview_image_url: Optional[str],
) -> dict[str, Any]:
    """Header, the non-empty metadata fields at top level, origins, source text, preview, processing."""
    response = {
        "contextName": result["contextName"],
        "schemaVersion": result["schemaVersion"],
        "metadataset": result["metadataset"],
        "metadataset_uri": metadataset_uri,
        "language": result["language"],
        "exportedAt": result["exportedAt"],
    }
    for key, value in result.get("metadata", {}).items():
        if value is not None and value != "" and value != [] and value != {}:
            response[key] = value
    if result.get("_origins"):
        response["_origins"] = result["_origins"]
    # For extended data upload and the next correction round
    if source_text:
        response["_source_text"] = source_text
    if preview_image_url:
        response["preview_image_url"] = preview_image_url
    response["processing"] = result["processing"]
    return response
