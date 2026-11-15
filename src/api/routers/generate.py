"""Full metadata generation (/generate)."""

import asyncio
import base64
import logging
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from ...metrics import count_generation
from ...models.schemas import (
    GenerateRequest,
    GenerateResponse,
    InputSource,
)
from ...services.input_source_service import TextExtractionError
from ...services.llm_prompts import correction_round
from ...services.metadata_service import GenerationOptions, MetadataService
from ...utils.loggable import loggable_url
from ...utils.schema_loader import get_content_type_uri
from .. import deps
from ..disconnect import (
    CLIENT_CLOSED_REQUEST,
    ClientDisconnected,
    run_unless_disconnected,
)
from ..generate_request import (
    long_text_as_new_source,
    parse_generate_request,
    texts_for_run,
)
from ..input_resolver import (
    HEADER_KEYS,
    MAX_CORRECTION_LENGTH,
    ResolvedInput,
    resolve_input,
    without_header,
)
from ..responses import (
    failure_response,
    generation_failed_response,
    generation_timeout_response,
)
from ..schema_resolution import resolve_schema
from .generate_docs import (
    GENERATE_DESCRIPTION,
    GENERATE_OPENAPI_EXTRA,
    GENERATE_RESPONSES,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# Main Endpoints
# ============================================================================


def _timeout_response():
    return generation_timeout_response(deps.settings.generate_timeout)


# /generate runs in flight in this process (METADATA_AGENT_MAX_CONCURRENT_GENERATIONS).
_running_generations = 0
# What a client is told to wait: a run takes 12-22 s as a rule.
_RETRY_AFTER_SECONDS = 15


def _too_many_generations() -> JSONResponse:
    # Not a run: the alert on 5xx answers leaves these out (alerts.yaml).
    count_generation("refused")
    response = failure_response(
        503,
        "Der Dienst erschließt gerade so viele Inhalte, wie er zugleich "
        f"bearbeitet. Bitte in {_RETRY_AFTER_SECONDS} s erneut versuchen.",
        {"code": "TOO_MANY_GENERATIONS", "message": "Zu viele Erschließungen zugleich"},
        # Turned away on purpose: worth seeing, but no fault of the service.
        log_level=logging.WARNING,
    )
    response.headers["Retry-After"] = str(_RETRY_AFTER_SECONDS)
    return response


def _client_gone() -> Response:
    """
    The run was stopped because nobody waits for it. Counted as `cancelled`,
    not as `failed`: a proxy with a short timeout would otherwise raise the
    alert on failed generations (audit 14.11., S-36).
    """
    count_generation("cancelled")
    logger.info("Client closed the connection: /generate stopped")
    return Response(status_code=CLIENT_CLOSED_REQUEST)


@router.post(
    "/generate",
    response_model=GenerateResponse,
    summary="Metadaten generieren",
    description=GENERATE_DESCRIPTION,
    responses=GENERATE_RESPONSES,
    openapi_extra=GENERATE_OPENAPI_EXTRA,
)
async def generate_metadata(request: Request):
    """
    Generate metadata from text.

    Extracts metadata from text using AI. Control characters are auto-sanitized.

    **Output Format** (flat, see GenerateResponse):
    - Header: contextName, schemaVersion, metadataset, metadataset_uri,
      language, exportedAt
    - every non-empty metadata field at top level under its field id
    - _origins, _source_text and preview_image_url where present
    - processing: Success status, stats, LLM info, errors/warnings
    """
    global _running_generations
    limit = deps.settings.max_concurrent_generations
    if limit is not None and _running_generations >= limit:
        return _too_many_generations()
    _running_generations += 1
    try:
        return await _generate(request)
    except ClientDisconnected:
        return _client_gone()
    finally:
        _running_generations -= 1


@dataclass
class _Prepared:
    """What resolving a request yields: all the run and its answer need."""

    req: GenerateRequest
    version: str
    schema_file: str
    existing_metadata: dict[str, Any]
    origins: Any
    text: str
    source_text: str
    # About the request and its source; they precede the run's own warnings.
    warnings: list[str]
    is_correction: bool
    service: MetadataService
    screenshot_task: Optional[asyncio.Task]

    async def release(self) -> None:
        """Stop the capture nobody will await, close the request's own client."""
        if self.screenshot_task:
            self.screenshot_task.cancel()
        if deps.has_own_llm_client(self.req):
            await self.service.llm_service.close()


async def _generate(request: Request):
    # One deadline for fetching the source and generating: together they must
    # stay below the ingress timeout (METADATA_AGENT_GENERATE_TIMEOUT).
    deadline = asyncio.get_running_loop().time() + deps.settings.generate_timeout

    prepared = await _resolve(request, deadline)
    if isinstance(prepared, Response):
        return prepared
    result = await _run(request, prepared, deadline)
    if isinstance(result, Response):
        return result
    return await _answer(prepared, result, deadline)


async def _resolve(request: Request, deadline: float) -> _Prepared | Response:
    """The request, its schema and its source - or the answer that ends it here."""
    req = await parse_generate_request(request)
    existing_metadata = without_header(req.existing_metadata or {})
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
    deps.check_model_override(req.llm_provider, req.llm_model)
    request_warnings: list[str] = []
    if original_source_text and req.text and len(req.text) > MAX_CORRECTION_LENGTH:
        original_source_text = None
        request_warnings.append(long_text_as_new_source(req))
    resolved = await _fetch_source(request, req, deadline)
    if isinstance(resolved, Response):
        return resolved
    # Values the caller sends win over those the node already has.
    existing_metadata = {
        **without_header(resolved.fetched_metadata),
        **existing_metadata,
    }
    text, source_text, cut = texts_for_run(
        req, resolved.text, original_source_text, existing_metadata
    )
    request_warnings += cut
    service = deps.get_metadata_service(
        llm_provider=req.llm_provider, llm_model=req.llm_model
    )
    # Runs in parallel to the extraction.
    screenshot_task = _start_screenshot(req, existing_metadata)
    return _Prepared(
        req=req,
        version=version,
        schema_file=schema_file,
        existing_metadata=existing_metadata,
        origins=origins,
        text=text,
        source_text=source_text,
        warnings=request_warnings + resolved.warnings,
        # The same condition texts_for_run uses for building the correction text.
        is_correction=bool(original_source_text and req.text and req.text.strip()),
        service=service,
        screenshot_task=screenshot_task,
    )


async def _run(
    request: Request, prepared: _Prepared, deadline: float
) -> dict[str, Any] | Response:
    """The extraction, inside the deadline and only while the client waits."""
    req = prepared.req
    try:
        async with asyncio.timeout_at(deadline):
            with correction_round(prepared.is_correction):
                return await run_unless_disconnected(
                    request,
                    prepared.service.generate_metadata(
                        text=prepared.text,
                        options=GenerationOptions(
                            context=req.context,
                            version=prepared.version,
                            schema_file=prepared.schema_file,
                            existing_metadata=prepared.existing_metadata,
                            language=req.language,
                            max_workers=req.max_workers,
                            include_core=req.include_core,
                            enable_geocoding=req.enable_geocoding,
                            normalize_output=req.normalize,
                            normalize_vocabularies=req.normalize,
                            regenerate_fields=req.regenerate_fields,
                            regenerate_empty=req.regenerate_empty,
                            origins=prepared.origins,
                            deadline=deadline,
                        ),
                    ),
                )
    except TimeoutError:
        # Field tasks were cancelled with the run (asyncio.timeout).
        count_generation("failed")
        await prepared.release()
        return _timeout_response()
    except BaseException as error:
        # A client that left (counted by generate_metadata) and a cancelled
        # request are no failed generation. The capture holds one of the few
        # browser slots for up to MAX_CAPTURE_SECONDS.
        if isinstance(error, Exception) and not isinstance(error, ClientDisconnected):
            count_generation("failed")
        await prepared.release()
        raise


async def _answer(
    prepared: _Prepared, result: dict[str, Any], deadline: float
) -> Response:
    """The run's result as the flat body with its preview - or as the failure."""
    if prepared.warnings:
        processing = result.setdefault("processing", {})
        processing["warnings"] = prepared.warnings + processing.get("warnings", [])

    count_generation((result.get("processing") or {}).get("status"))

    if (result.get("processing") or {}).get("status") == "failed":
        # Not one usable field: answer with the error instead of an empty
        # "success" that a client would apply over existing metadata.
        await prepared.release()
        return generation_failed_response(result)

    try:
        preview_image_url = await _screenshot_data_url(
            prepared.screenshot_task, deadline
        )
    finally:
        # Also when the wait is cancelled (CancelledError is no Exception).
        if deps.has_own_llm_client(prepared.req):
            await prepared.service.llm_service.close()

    metadataset_uri = get_content_type_uri(
        result["metadataset"], prepared.req.context, prepared.version
    )
    return JSONResponse(
        content=_flat_response(
            result, metadataset_uri, prepared.source_text, preview_image_url
        )
    )


async def _fetch_source(
    request: Request, req: GenerateRequest, deadline: float
) -> ResolvedInput | Response:
    """
    The source of the run, or the error answer when it cannot be had.

    A source that failed - text extraction, repository, the deadline - is a
    failed generation (audit 14.11., S-36). A request that names none, or a
    node that does not exist, is the caller's and no run.
    """
    try:
        async with asyncio.timeout_at(deadline):
            return await run_unless_disconnected(request, resolve_input(req))
    except TextExtractionError as e:
        if e.http_status >= 500:  # 422: the page has no text
            count_generation("failed")
        raise  # answered by UnexpectedErrorMiddleware
    except TimeoutError:
        count_generation("failed")
        return _timeout_response()
    except HTTPException as e:
        if e.status_code >= 500:
            count_generation("failed")
        raise


def _version_and_schema(req: GenerateRequest) -> tuple[str, str]:
    """
    The concrete schema version and schema file, as every endpoint resolves
    them (schema_resolution.py) - before the source is fetched (audit API-06).
    """
    return resolve_schema(req.context, req.version, req.schema_file)


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

    method = req.screenshot_method.value
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
    """
    Header, the non-empty metadata fields at top level, origins, source text,
    preview, processing. A metadata key named like a header key is left out:
    the header is this run's.
    """
    response = {
        "contextName": result["contextName"],
        "schemaVersion": result["schemaVersion"],
        "metadataset": result["metadataset"],
        "metadataset_uri": metadataset_uri,
        "language": result["language"],
        "exportedAt": result["exportedAt"],
    }
    for key, value in result.get("metadata", {}).items():
        if key in HEADER_KEYS:
            continue
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
