"""Saving an edited repository node (POST /nodes)."""

import asyncio

from fastapi import Request
from pydantic import ValidationError

from ...models.schemas import SaveNodeRequest, SaveNodeResponse
from ...services.repository_curation import (
    DEFAULT_WORKFLOW_STATUS,
    QUALITY_WORKFLOW_STATUS,
)
from ...services.repository_result import WriteResult


from fastapi import APIRouter

from .. import deps

from ..deps import (
    carry_schema_markers,
    parse_json_object,
    require_repository_service,
)
from ..openapi import documented_body_schema
from .request_body import invalid_body
from .upload import refused_write


router = APIRouter()


# ============================================================================
# Node Write Endpoint (create or update)
# ============================================================================


@router.post(
    "/nodes",
    response_model=SaveNodeResponse,
    tags=["Repository"],
    summary="Node speichern — anlegen oder aktualisieren",
    description="""Schreibt Metadaten ins WLO edu-sharing Repository: legt einen Node an oder aktualisiert einen bestehenden.

## Anlegen oder Aktualisieren

Der Unterschied ist `node_id`:

- **ohne `node_id`** → ein neuer Node wird im Eingangsordner angelegt
- **mit `node_id`** → genau dieser Node wird überschrieben

Beim Update gilt: **Felder, die nicht im Body stehen, bleiben unverändert.**
Geschrieben wird nur, was tatsächlich mitgeschickt wurde — leere Werte (`null`,
`""`, `[]`) zählen dabei als nicht gesetzt und löschen nichts.

Davon ausgenommen ist `ccm:oeh_extendedData`: das Feld hält die Metadaten als
einen JSON-Block und wird beim Schreiben komplett ersetzt. Bei einem Teil-Update
gingen die nicht mitgeschickten Felder darin also verloren — deshalb ist
`write_extended_data` hier standardmäßig **aus** und muss für einen vollständigen
Datensatz ausdrücklich eingeschaltet werden.

## Zwei-Stunden-Fenster

Ein Node kann nur innerhalb von **2 Stunden nach dem Anlegen** über diesen
Endpoint bearbeitet werden. Danach wird das Update mit **403** abgelehnt.
Gemessen wird die Erstellungszeit (`cm:created`), nicht die letzte Änderung —
das Fenster verlängert sich also durch Bearbeiten nicht.

Der Grund: nach dieser Zeit hat der Inhalt in der Regel die Redaktion erreicht,
und ein automatisches Überschreiben würde dort geleistete Arbeit stillschweigend
ersetzen. Für spätere Änderungen ist die Redaktionsoberfläche zuständig.

Abgelehnt wird ebenfalls mit `403`, wenn die Erstellungszeit nicht lesbar ist,
mit `404`, wenn der Node nicht existiert oder der Dienst keinen Zugriff hat, und
mit `502`, wenn das Repository ihn wegen eines Fehlers nicht liefern konnte.

## Fehlschlag

Wurde nichts geschrieben, kommt kein `200`, sondern das Fehlerformat wie bei
`/upload`: `422` `VALIDATION_ERROR` (Objektform eines Felds verletzt),
`422` `SCHEMA_UNAVAILABLE` (Kontext/Version unbekannt), `502` `UPSTREAM_ERROR`
(Repository lehnte Anlegen oder alle Metadaten ab oder war nicht erreichbar;
`discarded_node`/`node` nennen einen dabei angelegten Node, `field_errors` die
abgelehnten Felder). `200` mit `success: false` bleibt für Teilergebnisse, bei
denen der Node existiert.

## Body-Format

Wie bei `/upload`: die `/generate`-Antwort unverändert weiterreichen (flach,
Umschlag und Felder auf einer Ebene) oder die Felder unter `metadata`
verschachteln. Beide Formen schreiben dieselben Felder.

**`metadataset` in jedem Fall mitschicken.** Der Schlüssel entscheidet, welches
Typschema zusätzlich zu `core.json` gilt und damit, welche Felder überhaupt
geschrieben werden dürfen. Fehlt er, fallen typspezifische Felder wie
`ccm:oeh_event_begin` oder `ccm:price` stillschweigend weg.

## Optionale Parameter

| Parameter | Werte | Beschreibung |
|-----------|-------|-------------|
| `node_id` | Node-UUID | Node, der aktualisiert wird. Leer = neuen Node anlegen. |
| `collection_id` | Liste von IDs oder Sammlungs-URLs | Sammlung(en), in der/denen der Inhalt referenziert wird. Immer eine Liste. |
| `write_extended_data` | `false` (Standard), `true` | Extended-Felder schreiben (`ccm:oeh_extendedType`, `ccm:oeh_extendedData`, `ccm:oeh_extendedText`). |
| `extended_text` | String | Rohtext vor der Extraktion. Wird in `ccm:oeh_extendedText` geschrieben. Wirkt nur bei `write_extended_data: true`. |
| `preview` | Bild-URL **oder** Base64 | Vorschaubild des Nodes. Siehe unten. |
| `start_review_workflow` | `false` (Standard), `true` | Node zur Prüfung übergeben (`200_tocheck`). |
| `start_quality_workflow` | `false` (Standard), `true` | Qualität bestätigen (`140_ELEMENT_LEGALLY_APPROVED`). |

## Vorschaubild

`preview` nimmt beide Formen an und entscheidet am Wert selbst:

```jsonc
{ "preview": "https://example.org/hero.png" }        // wird geladen
{ "preview": "data:image/png;base64,iVBORw0KGgo…" }  // wird dekodiert
{ "preview": "iVBORw0KGgo…" }                        // nacktes Base64, auch ok
```

Das Bild wird als Vorschaubild an den Node gehängt (`POST .../preview`), nicht
als Metadatenfeld geschrieben. Erlaubt sind PNG, JPEG, GIF und WebP bis
10 MB; der Medientyp wird aus den ersten Bytes bestimmt, nicht aus der Data-URL
oder dem `Content-Type` der Quelle. Anderes (SVG, HTML) wird abgelehnt.

Ohne `preview` bleibt ein vorhandenes Vorschaubild unverändert — auch beim
Update. Ein Vorschaubild, das nicht geladen oder dekodiert werden kann, macht das
Schreiben der Metadaten **nicht** ungültig: `success` bleibt `true`, der Grund
steht in `preview.error` und zusätzlich in `error`.

## Workflows

Beide Workflow-Schalter sind **standardmäßig aus** — ohne ausdrückliche Angabe
wird kein Status gesetzt und der Node landet in keiner Warteschlange. Sind beide
gesetzt, werden sie in dieser Reihenfolge ausgeführt: erst `200_tocheck`, dann
`140_ELEMENT_LEGALLY_APPROVED`. Jeder Schritt erzeugt einen eigenen Eintrag in
der Workflow-Historie des Nodes, damit nachvollziehbar bleibt, wer was gesetzt
hat.

## Unterschiede zu `POST /upload`

`/upload` bleibt unverändert bestehen. Dieser Endpoint lässt weg, was nur beim
erstmaligen Hochladen sinnvoll ist: Dublettenprüfung, Screenshot/Vorschaubild
und die Wahl, ob der Node zurückgelesen wird. Der vollständige Node wird
**immer** als `node_full` mitgeliefert.

## Response

- **success**: `true` bei erfolgreichem Schreiben
- **node**: Node-Info mit ID und Repository-URL
- **node_full**: vollständiger edu-sharing Node, immer enthalten
- **node_created**: `true` beim Anlegen, `false` beim Update
- **preview**: Ergebnis des Vorschaubilds — nur wenn `preview` angegeben war
- **collections**: Ergebnis pro Sammlung — nur wenn Sammlungen angegeben waren
- **workflow**: Ergebnis pro Workflow-Schritt — nur wenn ein Workflow angefordert war
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": documented_body_schema(SaveNodeRequest),
                    "examples": {
                        "anlegen": {
                            "summary": "Node anlegen",
                            "description": "Ohne node_id wird ein neuer Node im Eingangsordner angelegt. Beim vollständigen Datensatz lohnt write_extended_data, weil der Node hier zum ersten Mal beschrieben wird.",
                            "value": {
                                "contextName": "default",
                                "schemaVersion": "2.0.0",
                                "metadataset": "learning_material.json",
                                "cclom:title": "Kernphysik – Aufbau und Verhalten von Atomkernen",
                                "ccm:wwwurl": "https://de.wikipedia.org/wiki/Kernphysik",
                                "cclom:general_language": ["de"],
                                "write_extended_data": True,
                                "extended_text": "Rohtext der Webseite vor der KI-Extraktion...",
                            },
                        },
                        "aktualisieren": {
                            "summary": "Node aktualisieren",
                            "description": "Mit node_id wird der bestehende Node überschrieben. Nur die mitgeschickten Felder ändern sich; nach 2 Stunden wird das Update mit 403 abgelehnt.",
                            "value": {
                                "contextName": "default",
                                "schemaVersion": "2.0.0",
                                "metadataset": "learning_material.json",
                                "node_id": "177fba93-3885-479e-bfba-933885c79e1c",
                                "cclom:general_keyword": [
                                    "Kernphysik",
                                    "Atomkerne",
                                    "Radioaktivität",
                                ],
                            },
                        },
                        "mit_preview": {
                            "summary": "Vorschaubild setzen",
                            "description": "preview nimmt eine Bild-URL oder das Bild selbst als Data-URL/Base64. Es wird als Vorschaubild an den Node gehängt, nicht als Metadatenfeld geschrieben.",
                            "value": {
                                "metadataset": "learning_material.json",
                                "node_id": "177fba93-3885-479e-bfba-933885c79e1c",
                                "preview": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/Atomkernbindungsenergie_RK01.png/500px-Atomkernbindungsenergie_RK01.png",
                            },
                        },
                        "mit_workflow": {
                            "summary": "Anlegen und Qualität bestätigen",
                            "description": "Beide Workflow-Schalter gesetzt: erst '200_tocheck', dann '140_ELEMENT_LEGALLY_APPROVED', jeder als eigener Eintrag in der Historie.",
                            "value": {
                                "contextName": "default",
                                "schemaVersion": "2.0.0",
                                "metadataset": "learning_material.json",
                                "cclom:title": "Bruchrechnung Klasse 6",
                                "ccm:wwwurl": "https://example.com/bruchrechnung",
                                "collection_id": [
                                    "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"
                                ],
                                "start_review_workflow": True,
                                "start_quality_workflow": True,
                            },
                        },
                    },
                }
            }
        }
    },
)
async def save_node(request: Request):
    """Create a node or update an existing one, and answer with the full node."""
    data = parse_json_object(await request.body(), '{"cclom:title": "…"}')

    # The flat form puts options and metadata fields on the same level, so the
    # options have to be lifted out by name before the rest becomes the metadata
    # dict. A name missing from this list is not rejected — it is filed away as a
    # metadata field and silently does nothing. Anything added to SaveNodeRequest
    # belongs here too.
    if not isinstance(data.get("metadata"), dict):
        options = {
            key: data.pop(key)
            for key in (
                "node_id",
                "collection_id",
                "write_extended_data",
                "extended_text",
                "preview",
                "start_review_workflow",
                "start_quality_workflow",
            )
            if key in data
        }
        data = {"metadata": data, **options}
    else:
        carry_schema_markers(data)

    try:
        req = SaveNodeRequest(**data)
    except ValidationError as e:
        raise invalid_body(e) from e

    repo_service = require_repository_service()

    context = req.metadata.get("contextName", "default")
    version = req.metadata.get("schemaVersion", "latest")

    # Handover first, release second — the order the editorial process runs in.
    workflow_steps = []
    if req.start_review_workflow:
        workflow_steps.append(DEFAULT_WORKFLOW_STATUS)
    if req.start_quality_workflow:
        workflow_steps.append(QUALITY_WORKFLOW_STATUS)

    raw = await repo_service.save_node(
        metadata=req.metadata,
        node_id=req.node_id,
        context=context,
        version=version,
        write_extended_data=req.write_extended_data,
        extended_text=req.extended_text,
        collection_ids=req.collection_id,
        workflow_steps=workflow_steps,
        preview=req.preview,
        # METADATA_AGENT_UPLOAD_TIMEOUT from now; the service reports what
        # happened to the node when it hits (audit D-04).
        deadline=asyncio.get_running_loop().time() + deps.settings.upload_timeout,
    )
    result = WriteResult.from_service(raw)
    refusal = refused_write(result)
    if refusal is not None:
        return refusal
    return result.response(SaveNodeResponse, result.preview)
