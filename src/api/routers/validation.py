"""Validation and Markdown export of metadata (/validate, /export/markdown)."""

import asyncio
from typing import Any, TypeVar

from fastapi import HTTPException, Request
from pydantic import ValidationError

from ...models.schemas import (
    ValidateRequest,
    ValidateResponse,
    ExportMarkdownRequest,
    ExportMarkdownResponse,
)
from ...utils.schema_loader import schema_file_of


from fastapi import APIRouter

from .. import deps
from ..request_body import invalid_body
from ..openapi import documented_body_schema
from ..schema_resolution import resolve_schema
from ..deps import (
    carry_schema_markers,
    parse_json_object,
)
from .validation_docs import EXPORT_MARKDOWN_RESPONSES, VALIDATE_RESPONSES


router = APIRouter()

# A record is a few kilobytes. Both endpoints work on every value of the body
# in memory; 8 MB of list values took /validate to the pod's memory limit
# (audit 14.11., S-30). The service-wide limit stays for the others.
MAX_BODY_BYTES = 2 * 1024 * 1024


async def _body(request: Request) -> bytes:
    """The raw body, or 413 above MAX_BODY_BYTES - before anything is parsed."""
    raw_body = await request.body()
    if len(raw_body) > MAX_BODY_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Anfrage zu groß: höchstens {MAX_BODY_BYTES} Bytes für "
            f"{request.url.path}.",
        )
    return raw_body


def _wrapped(data: dict[str, Any], options: tuple[str, ...]) -> dict[str, Any]:
    """
    The body as the request models take it: the record under "metadata", the
    options beside it.

    In the flat form (the output of /generate as it is) the options stand next
    to the fields. They were wrapped along and ignored (audit 13.11., API-15);
    no field of a record is called like one of them, field ids carry a prefix.
    """
    if isinstance(data.get("metadata"), dict):
        carry_schema_markers(data)
        return data
    lifted = {name: data.pop(name) for name in options if name in data}
    return {"metadata": data, **lifted}


RecordRequest = TypeVar("RecordRequest", ValidateRequest, ExportMarkdownRequest)


async def _request(
    request: Request, model: type[RecordRequest], options: tuple[str, ...]
) -> RecordRequest:
    """
    The request of /validate or /export/markdown: a body within the limit, the
    record flat or under "metadata", refused with 422 where the model says so.
    """
    data = _wrapped(
        parse_json_object(await _body(request), '{"metadata": {...}}'), options
    )
    try:
        return model(**data)
    except ValidationError as e:
        raise invalid_body(e)


def _schema(
    context: str, version: str, schema_file: str, metadata: dict[str, Any]
) -> tuple[str, str]:
    """
    Version and schema file the record is read with (404 for what does not
    exist). Without a schema named: the one the services fall back to.
    """
    return resolve_schema(
        context, version, schema_file, when_auto=schema_file_of(metadata)
    )


@router.post(
    "/validate",
    response_model=ValidateResponse,
    responses=VALIDATE_RESPONSES,
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

## Grenzen

- Body höchstens 2 MiB, sonst `413`.
- Je Feld und Prüfung werden höchstens 20 Befunde gelistet; ein weiterer Eintrag
  nennt die Zahl der nicht mehr geprüften Werte. Je Antwort höchstens 500 Fehler
  und 500 Warnungen, danach ein Eintrag mit `field_id: "_limit"`.
- Zitierte Werte sind auf 200 Zeichen gekürzt.

## Fehler

- `404` — Kontext, Version oder Schema-Datei gibt es nicht (aus den Optionen
  oder aus `contextName`, `schemaVersion`, `metadataset` des Datensatzes).
- `400` — unbekannte Schema-URI; Body ist kein JSON-Objekt.
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": documented_body_schema(ValidateRequest),
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
    req = await _request(
        request, ValidateRequest, ("context", "version", "schema_file")
    )
    context, version, schema_file, metadata = req.get_effective_params()
    version, schema_file = _schema(context, version, schema_file, metadata)

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
    responses=EXPORT_MARKDOWN_RESPONSES,
    summary="Metadaten als Markdown exportieren",
    description="""Konvertiert Metadaten in ein lesbares Markdown-Dokument mit Labels und Struktur.

## Einfache Nutzung

Kopiere einfach den kompletten Output von `/generate` direkt hier rein.

## Export-Format

Das generierte Markdown enthält:
- **Titel**: der Name des Inhaltstyps (z.B. `Veranstaltung`)
- **Feldgruppen** mit ihren Labels; jedes gefüllte Feld genau einmal. Felder
  einer Gruppe, die kein Schema deklariert, stehen am Ende unter
  „Weitere Angaben“
- **Werte** formatiert nach Feldtyp (Datum, Liste, Text etc.)
- **Vokabular-Labels** statt technischer IDs

`schema_used` nennt die Schema-Datei, mit der exportiert wurde.

## Grenzen und Fehler

- Body höchstens 2 MiB, sonst `413`.
- `404` — Kontext, Version oder Schema-Datei gibt es nicht; `400` — unbekannte
  Schema-URI.

## Verwendung

- Zur Dokumentation
- Zur menschlichen Review
- Zum Teilen mit Nicht-Technikern
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": documented_body_schema(ExportMarkdownRequest),
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
    req = await _request(
        request,
        ExportMarkdownRequest,
        ("context", "version", "schema_file", "include_empty"),
    )
    context, version, schema_file, language, metadata = req.get_effective_params()
    version, schema_file = _schema(context, version, schema_file, metadata)

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
