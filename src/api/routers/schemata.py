"""The schemas the agent knows (/info/...)."""

from fastapi import HTTPException

from ...models.schemas import (
    GenerateRequest,
    SchemataInfoResponse,
    ContextInfo,
    SchemaInfo,
)
from ...utils.schema_loader import (
    get_available_contexts,
    get_available_schemas,
    load_schema,
)


from fastapi import APIRouter

from ..params import Context, SchemaFile, Version
from .info_docs import (
    SCHEMA_DEFINITION_RESPONSES,
    SCHEMAS_RESPONSES,
    SCHEMATA_RESPONSES,
)


router = APIRouter()


@router.get(
    "/info/schemata",
    response_model=SchemataInfoResponse,
    summary="Verfügbare Schema-Kontexte",
    description="""Listet alle verfügbaren Schema-Kontexte mit ihren Versionen auf.

## Response

- **contexts**: Liste aller Kontexte mit Namen, Versionen und Default-Version
- **default_context**: Der Standard-Kontext (normalerweise `default`)

## Verwendung

Nutze diese Info um gültige Werte für `context` und `version` in anderen Endpoints zu finden.
""",
    responses=SCHEMATA_RESPONSES,
)
async def get_schemata_info():
    """Get information about available schemata."""
    contexts = get_available_contexts()

    return SchemataInfoResponse(
        contexts=[
            ContextInfo(
                name=c["name"],
                display_name=c["display_name"],
                versions=c["versions"],
                default_version=c["default_version"],
            )
            for c in contexts
        ],
        # The context a request without one gets - no setting changes that
        # (audit 14.11., O-45).
        default_context=GenerateRequest.model_fields["context"].default,
    )


@router.get(
    "/info/schemas/{context}/{version}",
    response_model=list[SchemaInfo],
    summary="Schemas für Kontext/Version",
    description="""Listet alle verfügbaren Schemas (Content-Types) für einen Kontext und Version.

## Path-Parameter

- **context**: Schema-Kontext, z.B. `default`
- **version**: Schema-Version, z.B. `2.0.0` oder `latest`

## Response

Liste von Schemas mit:
- **file**: Schema-Dateiname (z.B. `event.json`)
- **profile_id**: Profil-ID für edu-sharing
- **label**: Name des Inhaltstyps (`de`, `en`), z.B. `Veranstaltung`; für `core.json` der Dateiname
- **groups**: IDs der Feldgruppen, z.B. `["schedule", "attendance"]`
- **field_count**: Anzahl Felder

`404` für einen unbekannten Kontext oder eine unbekannte Version.
""",
    responses=SCHEMAS_RESPONSES,
)
async def get_schemas_for_version(context: Context, version: Version):
    """Get available schemas for a context and version."""
    try:
        schemas = get_available_schemas(context, version)
        return [
            SchemaInfo(
                file=s["file"],
                profile_id=s["profile_id"],
                label=s["label"],
                groups=s["groups"],
                field_count=s["field_count"],
            )
            for s in schemas
        ]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/info/schema/{context}/{version}/{schema_file}",
    summary="Schema-Definition abrufen",
    description="""Gibt die vollständige Schema-Definition als JSON zurück.

## Path-Parameter

- **context**: Schema-Kontext, z.B. `default`
- **version**: Schema-Version, z.B. `2.0.0` oder `latest`
- **schema_file**: Schema-Datei, z.B. `event.json`, `core.json`

## Response

Die Schema-Datei, wie sie ausgeliefert wird:
- **profileId**, **version**, **@context**: Kennung, Version und JSON-LD-Kontext des Schemas
- **groups**: Feldgruppen für die UI-Anzeige (`id`, `label`)
- **fields**: Alle Felder mit ID, Label, Gruppe, Datentyp, Vokabular etc.
- **output_template**: leere Vorlage eines Datensatzes

`404` für einen unbekannten Kontext, eine unbekannte Version oder Schema-Datei.
""",
    responses=SCHEMA_DEFINITION_RESPONSES,
)
async def get_schema_definition(
    context: Context, version: Version, schema_file: SchemaFile
):
    """Get full schema definition."""
    try:
        schema = load_schema(context, version, schema_file)
        return schema
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
