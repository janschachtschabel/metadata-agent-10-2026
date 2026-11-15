"""Reading a /generate request: its body, and the texts of the run it asks for."""

from typing import Any, Optional

from fastapi import HTTPException, Request
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from ..models.schemas import GenerateRequest, InputSource
from . import deps
from .deps import loads_lenient
from .input_resolver import (
    MAX_CORRECTION_LENGTH,
    truncate,
    truncation_warnings,
    with_correction,
)
from .request_body import invalid_body


async def parse_generate_request(request: Request) -> GenerateRequest:
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
                data = loads_lenient(body_str)
            except ValueError as e:  # invalid JSON, NaN
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

    except (HTTPException, ClientDisconnect):
        # ClientDisconnect: the client left before the end of the body (499).
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Request parsing error: {str(e)}")


def long_text_as_new_source(req: GenerateRequest) -> str:
    """
    The warning for a text over MAX_CORRECTION_LENGTH next to `_source_text`.

    The 2.1.1 widget sends _source_text with every run: as text input, a text
    this long is a new source pasted over the old one, not a correction (final
    audit 18.10.) - extracted as such instead of refused. With a URL or a node
    the source is fetched and the text can only be the instruction: 400, where
    it was dropped under this very warning (audit 14.11., S-39).
    """
    if req.input_source != InputSource.TEXT:
        raise HTTPException(
            status_code=400,
            detail=f"Korrekturanweisung zu lang: höchstens {MAX_CORRECTION_LENGTH} "
            "Zeichen.",
        )
    return (
        f"Text länger als {MAX_CORRECTION_LENGTH} Zeichen: als neue Quelle "
        "extrahiert, nicht als Korrekturanweisung zur bisherigen."
    )


def texts_for_run(
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
