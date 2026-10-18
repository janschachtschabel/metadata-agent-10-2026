"""
The text a request works on, wherever it comes from (audit A-01).

/detect-content-type, /extract-field and /generate accept the same input
sources: text, a URL (text extraction), a node (repository), or a node with its
URL as fallback. They used to resolve them in three copies that drifted - the
same failed fetch answered 400 in two and 502 in the third (API-01), and
/generate lost a correction instruction whenever the text was fetched (L-02).
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from ..models.schemas import InputSource
from ..services.input_source_service import TextExtractionError
from ..services.llm_prompts import CORRECTION_HEADER as CORRECTION_HEADER
from ..utils.public_url import PublicUrlError
from . import deps

logger = logging.getLogger(__name__)

# ~125k tokens - generous for long web pages, bounded for the LLM context.
MAX_TEXT_LENGTH = 500_000

# A correction is an instruction, not a second source text. It is never cut
# off, so without a bound of its own it could push the source out of the
# context (audit API-05).
MAX_CORRECTION_LENGTH = 10_000

PREVIOUS_VALUES_HEADER = (
    "--- BISHERIGER STAND (extrahierte Metadaten aus Durchlauf 1) ---"
)


@dataclass
class ResolvedInput:
    text: str
    # Metadata the node already has; callers let their own values win.
    fetched_metadata: dict[str, Any] = field(default_factory=dict)
    # How the text was obtained, where the client should know (node_url fallback).
    warnings: list[str] = field(default_factory=list)


def with_source_url(text: str, url: str | None) -> str:
    """Put the source URL first: the LLM reads ccm:wwwurl and path signals from it."""
    return f"Quell-URL / Source URL: {url}\n\n{text}" if url else text


def truncate(text: str, reserve: int = 0) -> str:
    """Cut text to MAX_TEXT_LENGTH, keeping `reserve` characters free for what follows."""
    limit = max(0, MAX_TEXT_LENGTH - reserve)
    if len(text) <= limit:
        return text
    logger.warning(f"⚠️ Text truncated from {len(text)} to {limit} characters")
    return text[:limit]


async def resolve_input(req) -> ResolvedInput:
    """
    Fetch the text a request names, untruncated.

    `req` has input_source, text, source_url, node_id, extraction_method,
    language and output_format. Raises HTTPException 400 for a missing
    parameter or a URL that is not public, 502 when the source cannot be read.
    TextExtractionError propagates: it has its own answer (responses.py).
    """
    source = req.input_source
    if source == InputSource.TEXT:
        return ResolvedInput(text=req.text or "")
    if source == InputSource.URL and not req.source_url:
        raise HTTPException(
            status_code=400, detail="source_url required for input_source='url'"
        )
    if source in (InputSource.NODE_ID, InputSource.NODE_URL) and not req.node_id:
        raise HTTPException(
            status_code=400,
            detail=f"node_id required for input_source='{source.value}'",
        )

    input_service = deps.get_input_source_service()
    try:
        if source == InputSource.URL:
            text = await input_service.fetch_from_url(
                req.source_url,
                req.extraction_method.value,
                lang=req.language,
                output_format=req.output_format.value,
            )
            return ResolvedInput(text=with_source_url(text, req.source_url))

        if source == InputSource.NODE_ID:
            data = await input_service.fetch_from_node_id(req.node_id)
            return ResolvedInput(
                text=with_source_url(data.text, data.source_url),
                fetched_metadata=data.existing_metadata or {},
            )

        data = await input_service.fetch_from_node_url(
            req.node_id,
            source_url=req.source_url or None,
            extraction_method=req.extraction_method.value,
            lang=req.language,
            output_format=req.output_format.value,
        )
        return ResolvedInput(
            text=with_source_url(data.text, data.source_url or req.source_url),
            fetched_metadata=data.existing_metadata or {},
            warnings=data.warnings,
        )
    except (HTTPException, TextExtractionError):
        raise
    except PublicUrlError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        # The source (text extraction, repository) failed, not the request. Its
        # message may name internal hosts: log only (audit S-06).
        logger.error(f"❌ Fetching the input failed: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch input: {type(e).__name__}",
        ) from e


def with_correction(
    source: str, existing_metadata: dict[str, Any], instruction: str
) -> tuple[str, str]:
    """
    Combine a source, the values extracted so far and a correction instruction.

    Returns (text for the LLM, source text for the next round). The instruction
    comes last and is never cut off: the source is truncated to make room. The
    next round's source is the source alone: the values already carry this
    correction (the widget sends them anew every round), and a relative one
    such as "shorter" would otherwise be applied twice. The widget also uploads
    it as ccm:oeh_extendedText, which should not hold editor instructions.
    """
    if len(instruction) > MAX_CORRECTION_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Korrekturanweisung zu lang: höchstens {MAX_CORRECTION_LENGTH} "
            "Zeichen.",
        )
    lines = []
    for key, value in existing_metadata.items():
        if key.startswith("_"):
            continue
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        lines.append(f"  {key}: {value}")
    previous = f"\n\n{PREVIOUS_VALUES_HEADER}\n" + ("\n".join(lines) or "(keine)")
    correction = f"\n\n{CORRECTION_HEADER}\n{instruction.strip()}"

    source = truncate(source, reserve=len(previous) + len(correction))
    logger.info(
        f"🔄 RE-EXTRACTION: source ({len(source)} chars) + {len(lines)} "
        f"extracted fields + instruction"
    )
    return source + previous + correction, source
