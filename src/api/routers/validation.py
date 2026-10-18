"""Validation and Markdown export of metadata (/validate, /export/markdown)."""

import asyncio

from fastapi import HTTPException, Request
from pydantic import ValidationError

from ...models.schemas import (
    ValidateRequest,
    ValidateResponse,
    ExportMarkdownRequest,
    ExportMarkdownResponse,
)
from ...utils.schema_loader import (
    get_latest_version,
    resolve_schema_file_or_uri,
)


from fastapi import APIRouter

from .. import deps
from .request_body import invalid_body
from ..deps import (
    carry_schema_markers,
    parse_json_object,
)


router = APIRouter()


@router.post(
    "/validate",
    response_model=ValidateResponse,
    summary="Metadaten validieren",
    description="""Validiert Metadaten gegen das Schema und prüft Pflichtfelder, Datentypen und Vokabular-Werte.

## Einfache Nutzung

Kopiere einfach den kompletten Output von `/generate` direkt hier rein – Context, Version und Schema werden automatisch erkannt.

## Validierungsprüfungen

- **Pflichtfelder**: Sind alle required Felder ausgefüllt?
- **Datentypen**: Stimmen die Werte mit den erwarteten Typen überein?
- **Vokabulare**: Sind nur erlaubte Vocabulary-Werte verwendet?
- **Format**: Sind Datumsangaben, URLs etc. korrekt formatiert?

## Response

- **valid**: `true` wenn alle Prüfungen bestanden
- **errors**: Liste der Validierungsfehler mit Feld-ID und Beschreibung
- **warnings**: Nicht-kritische Hinweise
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "examples": {
                        "direkt": {
                            "summary": "Direkter Output von /generate",
                            "description": "Kopiere einfach den kompletten Output von /generate hier rein",
                            "value": {
                                "contextName": "default",
                                "schemaVersion": "1.8.1",
                                "metadataset": "event.json",
                                "language": "de",
                                "cclom:title": "Workshop KI in der Bildung",
                                "cclom:general_description": "Ein Workshop über KI...",
                                "schema:actor": [{"name": "Max Mustermann"}],
                            },
                        }
                    },
                }
            }
        }
    },
)
async def validate_metadata(request: Request):
    """
    Validate metadata against schema.

    **Einfache Nutzung:** Kopiere einfach den kompletten Output von `/generate` direkt hier rein.

    Checks if the provided metadata conforms to the schema rules,
    including required fields, data types, and vocabulary constraints.

    **Auto-Detection**: Context, version, and schema are automatically detected from:
    - `contextName`, `schemaVersion`, `metadataset` (new flat format)
    """
    # Parse JSON body - accept both direct metadata or wrapped in "metadata" field
    data = parse_json_object(await request.body(), '{"metadata": {...}}')

    # If direct metadata (no "metadata" wrapper), wrap it for Pydantic model
    if "metadata" not in data or not isinstance(data.get("metadata"), dict):
        data = {"metadata": data}
    else:
        carry_schema_markers(data)

    # Validate with Pydantic model
    try:
        req = ValidateRequest(**data)
    except ValidationError as e:
        raise invalid_body(e)

    # Use get_effective_params for clean parameter extraction
    context, version, schema_file, metadata = req.get_effective_params()

    # Normalize version: strip leading "v" if present
    if version.startswith("v"):
        version = version[1:]
    # Resolve "latest" to actual version
    if version == "latest":
        version = get_latest_version(context)

    # Resolve schema_file: accept URI or filename
    try:
        schema_file = resolve_schema_file_or_uri(schema_file, context, version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    service = deps.get_metadata_service()
    # CPU-bound (vocabulary suggestions); keep it off the event loop (audit P-07).
    result = await asyncio.to_thread(
        service.validate_metadata,
        metadata=metadata,
        context=context,
        version=version,
        schema_file=schema_file,
    )

    return ValidateResponse(**result)


@router.post(
    "/export/markdown",
    response_model=ExportMarkdownResponse,
    summary="Metadaten als Markdown exportieren",
    description="""Konvertiert Metadaten in ein lesbares Markdown-Dokument mit Labels und Struktur.

## Einfache Nutzung

Kopiere einfach den kompletten Output von `/generate` direkt hier rein.

## Export-Format

Das generierte Markdown enthält:
- **Titel** des Inhalts
- **Feldgruppen** mit deutschen Labels
- **Werte** formatiert nach Feldtyp (Datum, Liste, Text etc.)
- **Vokabular-Labels** statt technischer IDs

## Verwendung

- Zur Dokumentation
- Zur menschlichen Review
- Zum Teilen mit Nicht-Technikern
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "examples": {
                        "direkt": {
                            "summary": "Direkter Output von /generate",
                            "description": "Kopiere einfach den kompletten Output von /generate hier rein",
                            "value": {
                                "contextName": "default",
                                "schemaVersion": "1.8.1",
                                "metadataset": "event.json",
                                "language": "de",
                                "cclom:title": "Workshop KI in der Bildung",
                                "cclom:general_description": "Ein Workshop über KI...",
                                "schema:actor": [{"name": "Max Mustermann"}],
                            },
                        }
                    },
                }
            }
        }
    },
)
async def export_markdown(request: Request):
    """
    Export metadata to human-readable Markdown.

    **Einfache Nutzung:** Kopiere einfach den kompletten Output von `/generate` direkt hier rein.

    Converts the metadata JSON to a formatted Markdown document
    with proper labels and structure.
    """
    # Parse JSON body - accept both direct metadata or wrapped in "metadata" field
    data = parse_json_object(await request.body(), '{"metadata": {...}}')

    # If direct metadata (no "metadata" wrapper), wrap it for Pydantic model
    if "metadata" not in data or not isinstance(data.get("metadata"), dict):
        data = {"metadata": data}
    else:
        carry_schema_markers(data)

    # Validate with Pydantic model
    try:
        req = ExportMarkdownRequest(**data)
    except ValidationError as e:
        raise invalid_body(e)

    # Use get_effective_params for clean parameter extraction
    context, version, schema_file, language, metadata = req.get_effective_params()

    # Normalize version: strip leading "v" if present
    if version.startswith("v"):
        version = version[1:]
    # Resolve "latest" to actual version
    if version == "latest":
        version = get_latest_version(context)

    # Resolve schema_file: accept URI or filename
    try:
        schema_file = resolve_schema_file_or_uri(schema_file, context, version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    service = deps.get_metadata_service()
    markdown = service.export_to_markdown(
        metadata=metadata,
        context=context,
        version=version,
        schema_file=schema_file,
        language=language,
        include_empty=req.include_empty,
    )

    return ExportMarkdownResponse(
        markdown=markdown,
        schema_used=schema_file,
    )
