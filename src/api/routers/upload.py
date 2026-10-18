"""Writing metadata to the repository (/upload)."""

import asyncio
from typing import Any, Optional

from fastapi import HTTPException, Request
from pydantic import ValidationError

from ...models.schemas import (
    UploadRequest,
    UploadResponse,
    UploadedNodeInfo,
    FieldUploadError,
    CollectionResult,
    WorkflowStepResult,
)
import logging


from fastapi import APIRouter

from ..responses import SAVE_NODE_ERROR_STATUS, upload_timeout_response

from .. import deps
from ..deps import (
    carry_schema_markers,
    parse_json_object,
    require_repository_service,
)
from ..openapi import documented_body_schema
from .request_body import invalid_body

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# Repository Upload Endpoint
# ============================================================================


@router.post(
    "/upload",
    response_model=UploadResponse,
    summary="Metadaten ins Repository hochladen",
    description="""Lädt Metadaten ins WLO edu-sharing Repository hoch und erstellt einen neuen Node.

## Einfache Nutzung

Kopiere einfach den kompletten Output von `/generate` direkt hier rein.

## Body-Format

**Empfohlen:** die `/generate`-Antwort unverändert weiterreichen. `/generate`
antwortet flach — Umschlag und Felder auf einer Ebene.

Zwei weitere Formen sind gleichwertig und schreiben dieselben Felder:

2. **Felder unter `metadata`** — so exportiert die Webkomponente
3. **Alles unter `metadata`** — doppelt verschachtelt

**`metadataset` in jedem Fall mitschicken.** Der Schlüssel entscheidet, welches
Typschema zusätzlich zu `core.json` gilt und damit, welche Felder überhaupt
geschrieben werden dürfen (nur core: 22 Felder, + `event.json`: 29,
+ `learning_material.json`: 34). Fehlt er, fallen typspezifische Felder wie
`ccm:oeh_event_begin` oder `ccm:price` stillschweigend weg.

## Optionale Parameter

| Parameter | Werte | Beschreibung |
|-----------|-------|-------------|
| `repository` | *(deprecated — ignoriert)* | Repository-URL wird über `METADATA_AGENT_REPOSITORY_URL` konfiguriert. Parameter wird aus Abwärtskompatibilität akzeptiert. |
| `check_duplicates` | `true` (Standard), `false` | Duplikat-Prüfung via ccm:wwwurl |
| `start_workflow` | `true` (Standard), `false` | Review-Workflow starten |
| `preview_url` | URL (leer = auto) | URL für Vorschaubild-Screenshot. Wenn leer, wird `ccm:wwwurl` aus den Metadaten verwendet. |
| `screenshot_method` | `playwright` (Standard), `pageshot` | Lokale Screenshot-Erfassung mit Playwright. `pageshot` ist ein Legacy-Alias und nimmt ebenfalls lokal auf. |
| `write_extended_data` | `true` (Standard), `false` | Extended-Felder schreiben (`ccm:oeh_extendedType`, `ccm:oeh_extendedData`, `ccm:oeh_extendedText`). |
| `extended_text` | String (leer = auto) | Rohtext vor der Extraktion. Wird in `ccm:oeh_extendedText` geschrieben. |
| `return_full_node` | `false` (Standard), `true` | Node nach dem Schreiben zurücklesen und als `node_full` mitliefern. |
| `collection_id` | Liste von IDs oder Sammlungs-URLs | Sammlung(en), in der/denen der Inhalt referenziert wird. Immer eine Liste, auch bei einer Sammlung. |
| `workflow_steps` | Liste von Status | Workflow-Status, die nach dem Upload der Reihe nach gesetzt werden. Standard: `["200_tocheck"]`. |
| `workflow_comment` | String | Kommentar zu jedem Workflow-Schritt. |
| `workflow_receiver` | Liste von Authority-Namen | Empfänger jedes Workflow-Schritts. |

## Workflow

1. **Duplikat-Check** (optional): Prüft ob URL bereits existiert
2. **Node erstellen**: Legt neuen Node mit Basisdaten an
3. **Metadaten setzen**: Überträgt alle Metadaten
4. **Collections**: Referenziert den Node in Sammlungen (`collection_id` und/oder Sammlungen aus den Metadaten)
5. **Extended Fields** (optional): Schreibt `ccm:oeh_extendedType` (Inhaltstyp-URI), `ccm:oeh_extendedData` (Metadaten-JSON), `ccm:oeh_extendedText` (Rohtext)
6. **Vorschaubild**: Der parallel aufgenommene Screenshot wird **vor** dem Workflow gespeichert, damit der Node mit Bild in der Prüfung landet. Reicht die Zeit nicht (siehe Zeitlimit), wird ohne Vorschau weitergemacht und `preview.success: false` gemeldet.
7. **Workflow** (optional): Durchläuft `workflow_steps` Schritt für Schritt
8. **Node zurücklesen** (optional, `return_full_node`): Liest den fertigen Node erneut aus

## Zeitlimit

Der Schreibvorgang endet nach `METADATA_AGENT_UPLOAD_TIMEOUT` Sekunden (Standard 100)
mit **504** und `error.code: UPLOAD_TIMEOUT`. Die Antwort sagt, was mit dem Node
geschah: `discarded_node` — der dabei angelegte, unvollständige Node wurde verworfen;
`node` — der Node bleibt erhalten (bereits im Workflow, nicht verwerfbar oder per
`node_id` übergeben). Ein vollständig geschriebener Node wird nie verworfen.

## Sammlungen

`collection_id` ist **immer eine Liste** — auch bei einer einzelnen Sammlung.
Die Einträge sind IDs oder kopierte Sammlungs-URLs
(`.../components/collections?id=<uuid>`). Ein nackter String wird mit `422`
abgelehnt. Der Node wird als Referenz in die Sammlung gelegt — das Original
bleibt im Inbox-Ordner.
Zusätzlich werden `virtual:collection_id_primary` und `ccm:collection_id`
aus den Metadaten berücksichtigt; Duplikate werden entfernt.

## Prüf-Workflow

Standardmäßig endet der Upload bei `200_tocheck` — der Node liegt damit zur
Prüfung durch Menschen bereit. Über `workflow_steps` lässt sich der
Redaktions-Workflow weiter durchlaufen, jeder Schritt als eigener Eintrag in
der Workflow-Historie:

```jsonc
{ "workflow_steps": ["200_tocheck", "140_ELEMENT_LEGALLY_APPROVED"] }
```

Für bereits hochgeladene Nodes gibt es dafür `POST /workflow/{node_id}`.

## Response

- **success**: `true` bei erfolgreichem Upload
- **duplicate**: `true` wenn URL bereits existiert
- **node**: Node-Info mit ID und URL
- **node_full**: Vollständiger edu-sharing Node — nur bei `return_full_node: true`
- **collections**: Ergebnis pro Sammlung — nur wenn Sammlungen angegeben waren
- **workflow**: Ergebnis pro Workflow-Schritt — nur bei `start_workflow: true`

### `node_full`

Enthält den Node exakt so, wie ihn das Repository liefert — gleiche Struktur wie das
`node`-Objekt von `GET /node/v1/nodes/-home-/{id}/metadata` bzw. der Antwort von
`createChild`. Gedacht für Clients, die den Node nicht selbst nachlesen können, weil
sie als Gast keinen Repository-Zugriff haben.

Die Felder liegen dort in edu-sharing-Notation, nicht in der verkürzten Form von `node`:

| `node` | `node_full` |
|--------|-------------|
| `nodeId` | `ref.id` |
| `title` | `properties["cclom:title"][0]` |
| `wwwurl` | `properties["ccm:wwwurl"][0]` |

Kostet einen zusätzlichen Repository-Aufruf. Schlägt der Lesevorgang fehl, bleibt
`node_full` leer — der Upload selbst gilt weiterhin als erfolgreich.
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": documented_body_schema(UploadRequest),
                    "examples": {
                        "direkt": {
                            "summary": "Direkter Output von /generate",
                            "description": "Kopiere den Output von /generate hier rein. Die Optionen darunter sind alle freiwillig — leer gelassen gilt jeweils der Standard.",
                            "value": {
                                "contextName": "default",
                                "schemaVersion": "1.8.1",
                                "metadataset": "event.json",
                                "cclom:title": "Workshop KI in der Bildung",
                                "ccm:wwwurl": "https://example.com/workshop",
                                "check_duplicates": True,
                                "start_workflow": True,
                                "node_id": "",
                                "collection_id": [],
                                "preview_url": "",
                                "screenshot_method": "playwright",
                                "write_extended_data": True,
                                "extended_text": "Rohtext der Webseite vor der KI-Extraktion...",
                            },
                        },
                        "mit_sammlung_und_workflow": {
                            "summary": "Upload in eine Sammlung + Qualität bestätigen",
                            "description": "Referenziert den Inhalt in einer Sammlung und durchläuft den Prüf-Workflow bis 'Qualität bestätigt'.",
                            "value": {
                                "contextName": "default",
                                "schemaVersion": "2.0.0",
                                "metadataset": "learning_material.json",
                                "cclom:title": "Bruchrechnung Klasse 6",
                                "ccm:wwwurl": "https://example.com/bruchrechnung",
                                "collection_id": [
                                    "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9"
                                ],
                                "workflow_steps": [
                                    "200_tocheck",
                                    "140_ELEMENT_LEGALLY_APPROVED",
                                ],
                                "workflow_comment": "Redaktionell geprüft",
                            },
                        },
                        "in_bestehenden_node": {
                            "summary": "Zweistufig — in einen vorhandenen Node schreiben",
                            "description": "Zweiter Schritt nach POST /node: die dort gelieferte Node-ID hier als node_id angeben. Es wird kein neuer Node angelegt, die Dublettenprüfung entfällt, und bei einem Fehler bleibt der Node erhalten.",
                            "value": {
                                "contextName": "default",
                                "schemaVersion": "2.0.0",
                                "metadataset": "learning_material.json",
                                "cclom:title": "Bruchrechnung Klasse 6",
                                "ccm:wwwurl": "https://example.com/bruchrechnung",
                                "node_id": "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9",
                            },
                        },
                    },
                }
            }
        }
    },
)
async def upload_to_repository(request: Request):
    """
    Upload metadata to WLO edu-sharing repository.

    **Einfache Nutzung:** Kopiere einfach den kompletten Output von `/generate` direkt hier rein.

    Optional kannst du Optionen mit übergeben:
    - `check_duplicates`: true (default) oder false
    - `start_workflow`: true (default) oder false

    The steps are RepositoryService's (see its docstring): duplicate check,
    node, metadata, collections, extended fields, preview, workflow, read-back.
    """
    data = _wrapped(parse_json_object(await request.body(), '{"cclom:title": "…"}'))
    try:
        req = UploadRequest(**data)
    except ValidationError as e:
        raise invalid_body(e) from e

    repo_service = require_repository_service()

    # Extract context/version from metadata (before potential unwrapping)
    context = req.metadata.get("contextName", "default")
    version = req.metadata.get("schemaVersion", "latest")

    # Runs in parallel with the upload; awaited inside upload_metadata before
    # the workflow, so the node enters the editorial queue with its preview.
    capture = _PreviewCapture.start(req, _fields_with_source(req))

    try:
        result = await repo_service.upload_metadata(
            metadata=req.metadata,
            check_duplicates=req.check_duplicates,
            start_workflow=req.start_workflow,
            context=context,
            version=version,
            write_extended_data=req.write_extended_data,
            extended_text=req.extended_text,
            return_full_node=req.return_full_node,
            collection_ids=req.collection_id,
            node_id=req.node_id,
            workflow_steps=req.workflow_steps,
            workflow_comment=req.workflow_comment,
            workflow_receiver=req.workflow_receiver,
            preview_source=capture.source,
            deadline=_deadline(),
        )
    except BaseException:
        # As in /generate: nobody awaits the capture any more.
        capture.cancel()
        raise

    if result.get("error_code") == "upload_timeout":
        capture.cancel()
        return upload_timeout_response(deps.settings.upload_timeout, result)

    # A handed-over node that may not be written is refused like in /nodes.
    error_code = result.get("error_code")
    if error_code in SAVE_NODE_ERROR_STATUS:
        capture.cancel()
        raise HTTPException(
            status_code=SAVE_NODE_ERROR_STATUS[error_code],
            detail=result.get("error", "Node kann nicht bearbeitet werden"),
        )

    preview_status = capture.status(result.get("preview"))
    capture.settle()
    return _upload_response(result, preview_status)


def _deadline() -> float:
    """METADATA_AGENT_UPLOAD_TIMEOUT from now, in event loop time (audit D-04)."""
    return asyncio.get_running_loop().time() + deps.settings.upload_timeout


# Options of the flat body form and their defaults. They have to be lifted out
# by name before the rest becomes the metadata dict. A name missing here is not
# rejected — it is filed away as a metadata field and silently does nothing,
# which is worse than an error. Anything added to UploadRequest belongs here too.
_UPLOAD_OPTIONS: dict[str, Any] = {
    "node_id": None,
    "repository": "staging",
    "check_duplicates": True,
    "start_workflow": True,
    "source": None,
    "preview_url": None,
    "screenshot_method": "playwright",
    "write_extended_data": True,
    "extended_text": None,
    "return_full_node": False,
    "collection_id": None,
    "workflow_steps": None,
    "workflow_comment": None,
    "workflow_receiver": None,
}


def _wrapped(data: dict[str, Any]) -> dict[str, Any]:
    """The body as UploadRequest takes it: metadata under 'metadata', options beside."""
    if isinstance(data.get("metadata"), dict):
        carry_schema_markers(data)
        return data
    options = {key: data.pop(key, default) for key, default in _UPLOAD_OPTIONS.items()}
    options["screenshot_method"] = options["screenshot_method"] or "playwright"
    return {"metadata": data, **options}


def _fields_with_source(req: UploadRequest) -> dict[str, Any]:
    """
    The metadata fields, with the `source` override applied.

    The web component sends a nested form (metadata.metadata); the original
    wrapper stays for _write_extended_fields, the fields are worked on inside.
    """
    inner = req.metadata.get("metadata")
    fields = inner if isinstance(inner, dict) else req.metadata
    if req.source:
        fields["ccm:oeh_publisher_combined"] = req.source
    return fields


class _PreviewCapture:
    """The screenshot that runs next to the upload and becomes the node's preview."""

    def __init__(self, task: Optional[asyncio.Task]):
        self.task = task
        self.shot: Any = None

    @classmethod
    def start(cls, req: UploadRequest, fields: dict[str, Any]) -> "_PreviewCapture":
        """preview_url, else ccm:wwwurl of the record; no URL, no capture."""
        preview_url = req.preview_url
        if not preview_url:
            wwwurl = fields.get("ccm:wwwurl")
            if isinstance(wwwurl, list):
                wwwurl = wwwurl[0] if wwwurl else None
            if isinstance(wwwurl, str) and wwwurl.startswith("http"):
                preview_url = wwwurl
        if not preview_url:
            return cls(None)
        screenshot_service = deps.get_screenshot_service()
        logger.info(
            f"📸 Starting async screenshot capture: {preview_url} (method: {req.screenshot_method.value})"
        )
        return cls(
            asyncio.create_task(
                screenshot_service.capture(
                    url=preview_url, method=req.screenshot_method.value
                )
            )
        )

    @property
    def source(self):
        """The preview source for upload_metadata, None without a capture."""
        return self._image if self.task else None

    async def _image(self) -> tuple[bytes, str]:
        if self.task is None:  # source is only handed out with a task
            raise RuntimeError("no screenshot capture to await")
        shot = await self.task
        self.shot = shot
        logger.info(
            f"📸 Screenshot ready: {shot.size_bytes} bytes ({shot.capture_time_ms}ms)"
        )
        return shot.image_bytes, shot.mimetype

    def cancel(self) -> None:
        if self.task:
            self.task.cancel()

    def status(self, stored: Optional[dict]) -> Optional[dict]:
        """The preview entry of the answer: how the stored image was captured."""
        if stored and stored.get("success") and self.shot:
            return {
                "success": True,
                "method": self.shot.method,
                "capture_time_ms": self.shot.capture_time_ms,
                "size_bytes": self.shot.size_bytes,
            }
        return stored

    def settle(self) -> None:
        """The upload ended before it needed the capture (e.g. a failed write)."""
        if not self.task or self.shot is not None:
            return
        if not self.task.done():
            self.task.cancel()
        elif not self.task.cancelled():
            self.task.exception()  # fetched, so asyncio does not warn about it


def _upload_response(
    result: dict[str, Any], preview_status: Optional[dict]
) -> UploadResponse:
    node_info = UploadedNodeInfo(**result["node"]) if result.get("node") else None
    raw_errors = result.get("field_errors")
    field_errors = [FieldUploadError(**e) for e in raw_errors] if raw_errors else None
    collections = (
        [CollectionResult(**c) for c in result["collections"]]
        if result.get("collections") is not None
        else None
    )
    workflow = (
        [WorkflowStepResult(**step) for step in result["workflow"]]
        if result.get("workflow") is not None
        else None
    )
    return UploadResponse(
        success=result.get("success", False),
        duplicate=result.get("duplicate"),
        node=node_info,
        node_full=result.get("node_full"),
        error=result.get("error"),
        step=result.get("step"),
        fields_written=result.get("fields_written"),
        fields_skipped=result.get("fields_skipped"),
        node_created=result.get("node_created"),
        schema_used=result.get("schema_used"),
        repo_fields_available=result.get("repo_fields_available"),
        field_errors=field_errors,
        preview=preview_status,
        collections=collections,
        workflow=workflow,
        discarded_node=result.get("discarded_node"),
    )
