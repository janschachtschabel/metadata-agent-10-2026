"""Creating a node and setting its workflow (/node, /workflow/{node_id})."""

import asyncio

from fastapi import HTTPException, Request
from pydantic import ValidationError

from ...models.schemas import (
    CreateNodeRequest,
    CreateNodeResponse,
    UploadedNodeInfo,
    WorkflowStepResult,
    WorkflowRequest,
    WorkflowResponse,
)


from fastapi import APIRouter

from ..responses import upload_timeout_response
from .request_body import invalid_body

from .. import deps
from ..deps import (
    require_node_id,
    parse_json_object,
    require_repository_service,
)


router = APIRouter()


# ============================================================================
# Node Creation Endpoint
# ============================================================================


@router.post(
    "/node",
    response_model=CreateNodeResponse,
    tags=["Repository"],
    summary="Node anlegen, ohne Metadaten zu schreiben",
    description="""
Legt einen Node im Eingangsordner an und gibt seine ID zurück — die erste Hälfte
eines Uploads für sich genommen.

**Wofür.** Wer eine Node-ID braucht, bevor die Metadaten existieren — um sie
anzuzeigen, zu verlinken oder einer Redaktion zu übergeben — bekommt sie hier und
reicht sie später als `node_id` an `POST /upload` weiter — innerhalb von 2 Stunden,
danach lehnt `/upload` den Node mit `403` ab. Ohne diesen Schritt legt
jeder `/upload` seinen eigenen Node an.

**Was geschrieben wird.** Genau die Felder, die der Upload beim Anlegen setzt:
`cclom:title` (Pflicht), `cclom:general_description`, `cclom:general_keyword`,
`ccm:wwwurl`, `cclom:general_language` — dazu `ccm:linktype: USER_GENERATED`.
Alles andere wird ignoriert; es braucht den Schemafilter, und der gehört in den
zweiten Schritt.

**Der Titel ist Pflicht.** Ein Node ohne Titel ist im Eingangsordner nicht
auffindbar, und der Zweck dieses Aufrufs ist gerade, etwas Referenzierbares zu
bekommen.

**Verlorene Antwort, Zeitlimit.** Geht die Antwort des Repositorys verloren,
wird der Node im Eingangsordner gesucht und – gefunden – normal zurückgegeben.
Nach `METADATA_AGENT_UPLOAD_TIMEOUT` Sekunden antwortet der Endpoint mit `504`
(`UPLOAD_TIMEOUT`); ein inzwischen gefundener Node steht dann in `node`.

```jsonc
// 1. Node anlegen
POST /node  { "metadata": { "cclom:title": "Bruchrechnung Klasse 6" } }
// → { "success": true, "node": { "nodeId": "5443240c-…" } }

// 2. später: Metadaten nachreichen
POST /upload { "metadata": { …aus /generate… }, "node_id": "5443240c-…" }
// → { "success": true, "node_created": false, "fields_written": 25 }
```

**Zwei Unterschiede zum einstufigen Upload**, wenn `node_id` gesetzt ist:
die Dublettenprüfung entfällt (der Node trägt die URL bereits und würde sich
selbst finden), und ein Fehler verwirft den Node **nicht** — er gehört dem
Aufrufer, nicht diesem Dienst.
""",
)
async def create_node(req: CreateNodeRequest):
    """Create a node with minimal fields and return its id."""
    repo_service = require_repository_service()

    data = req.metadata
    if "metadata" in data and isinstance(data.get("metadata"), dict):
        data = data["metadata"]

    result = await repo_service.create_node(
        metadata=data,
        check_duplicates=req.check_duplicates,
        # METADATA_AGENT_UPLOAD_TIMEOUT from now, like /upload and /nodes.
        deadline=asyncio.get_running_loop().time() + deps.settings.upload_timeout,
    )
    if result.get("error_code") == "upload_timeout":
        return upload_timeout_response(deps.settings.upload_timeout, result)

    if not result.get("success") and not result.get("duplicate"):
        # Only a missing title is the caller's; everything else failed upstream.
        raise HTTPException(
            status_code=400 if result.get("error_code") == "title_missing" else 502,
            detail=result.get("error", "Node konnte nicht angelegt werden"),
        )

    node = result.get("node")
    return CreateNodeResponse(
        success=result.get("success", False),
        duplicate=result.get("duplicate"),
        node=UploadedNodeInfo(**node) if node else None,
        error=result.get("error"),
    )


# ============================================================================
# Workflow Endpoint
# ============================================================================


@router.post(
    "/workflow/{node_id}",
    response_model=WorkflowResponse,
    summary="Prüf-Workflow eines Nodes schrittweise weiterführen",
    description="""Setzt die Workflow-Status eines bereits hochgeladenen Nodes — Schritt für Schritt.

`POST /upload` endet standardmäßig bei `200_tocheck`: Der Inhalt liegt damit zur
Prüfung durch Menschen bereit. Dieser Endpoint führt den Redaktions-Workflow von
dort aus weiter.

## Warum Schritt für Schritt

edu-sharing legt für **jeden** PUT einen eigenen Eintrag in der Workflow-Historie
des Nodes an — mit Status, Kommentar, Zeitpunkt und ausführendem Nutzer. Genau
darüber lässt sich später abfragen, wer die Qualität bestätigt hat. Ein Sprung
direkt auf den Endstatus würde diese Spur verlieren.

Ausgeführt wird unter dem konfigurierten Service-Account
(`METADATA_AGENT_WLO_GUEST_USERNAME`) — in der Historie erscheint also dieser
Nutzer, bei Gast-Zugang entsprechend der hinterlegte Gast-Nutzer.

## Status des WLO-Redaktions-Workflows

| Status | Bedeutung |
|--------|-----------|
| `100_unchecked` | Ungeprüft |
| `110_METADATA_RECORD_REQUESTED` | Metadaten-Erfassung angefordert |
| `120_METADATA_QUALITY_CONFIRMED` | Metadaten-Qualität bestätigt |
| `125_METADATA_QUALITY_FOR_BUFFET` | Für Redaktionsbuffet qualifiziert |
| `130_ELEMENT_REJECTED` | Element abgelehnt |
| `140_ELEMENT_LEGALLY_APPROVED` | Qualität bestätigt / Element freigegeben |
| `150_PUBLISH_IN_SEARCH` | In der Suche veröffentlicht |
| `160_REMOVE_FROM_SEARCH` | Aus der Suche entfernt |
| `200_tocheck` | Zur Prüfung übergeben (Standard nach `/upload`) |
| `TASK_CREATE_TREE`, `TASK_CHECK_COLLECTION_PROPOSAL`, `TASK_CHECK_QUALITY` | Aufgaben-Status |

Die Liste stammt aus der Repository-Konfiguration (`/rest/config/v1/values`).
Unbekannte Status werden mit `422` abgelehnt, damit ein Tippfehler nicht als
Status im Repository landet.

## Response

- **steps**: Ergebnis pro Schritt in der ausgeführten Reihenfolge
- **current_status**: `ccm:wf_status`, aus dem Repository zurückgelesen
- **history**: Workflow-Historie des Nodes — enthält den ausführenden Nutzer je Eintrag
""",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/WorkflowRequest"},
                    "examples": {
                        "qualitaet_bestaetigen": {
                            "summary": "1. Qualität bestätigen",
                            "description": "Setzt den Node auf '140_ELEMENT_LEGALLY_APPROVED'.",
                            "value": {
                                "steps": ["140_ELEMENT_LEGALLY_APPROVED"],
                                "comment": "",
                            },
                        },
                        "mehrere_schritte": {
                            "summary": "2. Mehrere Schritte nacheinander",
                            "description": "Jeder Schritt wird einzeln gesetzt und einzeln protokolliert.",
                            "value": {
                                "steps": [
                                    "120_METADATA_QUALITY_CONFIRMED",
                                    "140_ELEMENT_LEGALLY_APPROVED",
                                    "150_PUBLISH_IN_SEARCH",
                                ],
                                "comment": "Redaktionell geprüft",
                            },
                        },
                    },
                }
            },
        }
    },
)
async def set_node_workflow(node_id: str, request: Request):
    """Advance an existing node through the editorial review workflow."""
    require_node_id(node_id)

    repo_service = require_repository_service()

    raw_body = await request.body()
    if not raw_body or not raw_body.strip():
        raise HTTPException(
            status_code=400,
            detail='Body erforderlich, z.B. {"steps": ["140_ELEMENT_LEGALLY_APPROVED"]}.',
        )

    data = parse_json_object(raw_body, '{"steps": ["140_ELEMENT_LEGALLY_APPROVED"]}')

    try:
        req = WorkflowRequest(**data)
    except ValidationError as e:
        raise invalid_body(e) from e

    result = await repo_service.set_workflow(
        node_id=node_id,
        steps=req.steps,
        comment=req.comment,
        receiver=req.receiver,
    )

    steps = None
    if result.get("steps") is not None:
        steps = [WorkflowStepResult(**s) for s in result["steps"]]

    return WorkflowResponse(
        success=result.get("success", False),
        nodeId=result.get("nodeId", node_id),
        steps=steps,
        current_status=result.get("current_status"),
        history=result.get("history"),
        repositoryUrl=result.get("repositoryUrl"),
        error=result.get("error"),
    )
