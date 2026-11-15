"""Comparing a written node with what was sent (/upload/verify/{node_id})."""

from fastapi import HTTPException, Request
from pydantic import ValidationError

from ...models.schemas import (
    VerifyRequest,
    VerifyResponse,
    FieldDiff,
)


from fastapi import APIRouter

from ..params import NodeId

from ..deps import (
    require_node_id,
    parse_json_object,
    require_repository_service,
)
from ..responses import SAVE_NODE_ERROR_STATUS
from ..request_body import invalid_body
from .repository_docs import VERIFY_RESPONSES


router = APIRouter()


# ============================================================================
# Upload Verification Endpoint
# ============================================================================


@router.post(
    "/upload/verify/{node_id}",
    response_model=VerifyResponse,
    responses=VERIFY_RESPONSES,
    summary="Upload-Ergebnis prüfen (SOLL/IST-Vergleich)",
    description="""Liest die Metadaten eines Nodes aus dem Repository und vergleicht sie optional mit den erwarteten Werten.

## Nutzung

**Nur lesen** (ohne Body): Gibt die aktuellen Metadaten des Nodes zurück.

**SOLL/IST-Vergleich** (mit Body): Vergleicht die erwarteten Metadaten (z.B. Output von `/generate`) mit den tatsächlich geschriebenen Werten. `expected_metadata` nimmt die Formen an, die auch `/upload` annimmt: die Felder flach oder verschachtelt unter `metadata` bzw. `metadata.metadata` — alle Formen werden gleich verglichen. Optionen von `/upload` und `/nodes` neben den Feldern zählen nicht als Felder.

## Diff-Status pro Feld

| Status | Bedeutung |
|--------|-----------|
| `match` | Wert stimmt überein. Verglichen wird nach Art des Feldes: bei Vokabular-Feldern und beim Lizenzschlüssel ohne Rücksicht auf Groß- und Kleinschreibung, bei Freitext mit. Ein einzelner Wert gleicht einer Liste mit einem Eintrag, die Reihenfolge einer Liste zählt nicht, ein Datum darf als Millisekunden gespeichert sein. Ein Feld, das der Dienst in anderer Form schreibt, wird in dieser Form verglichen: `oeh:new_lrt` als `ccm:oeh_lrt`, ein Lizenz-Link in `ccm:custom_license` als `ccm:commonlicense_key` und `ccm:commonlicense_cc_version`, `cm:author` über die Namen |
| `mismatch` | Wert weicht ab (SOLL ≠ IST) |
| `missing_in_repo` | Feld wurde erwartet, ist aber nicht im Repository |
| `extra_in_repo` | Feld ist im Repository, war aber nicht erwartet. Was der Dienst aus gesendeten Feldern ableitet (Lizenzschlüssel und -version aus einem Lizenz-Link, die Autoren-Felder aus `cm:author`), zählt nicht dazu |
| `not_written` | Feld wird nie geschrieben: Es ist kein Repository-Feld des Schemas (weder unter seinem Namen noch unter dem, unter dem es geschrieben wird — `oeh:new_lrt` zählt als `ccm:oeh_lrt`), oder es ist ein `virtual:`-Feld, das das Repository selbst berechnet. Sind die Repository-Felder des Schemas nicht lesbar, gilt das für jedes Feld |

## Summary

Zählt die Anzahl der Felder pro Status-Kategorie. `unresolved` zählt daneben die verglichenen Felder, deren Wert das Repository nicht auflöst — ein solches Feld kann zugleich `match` sein.

## Fehler

- `404` — der Node existiert nicht oder das Dienstkonto hat keinen Zugriff (Repository `403`/`404`)
- `422` — der Body passt nicht zu `VerifyRequest`; `detail` nennt Feld und Grund
- `502` — das Repository konnte den Node wegen eines Fehlers nicht liefern
""",
    openapi_extra={
        "requestBody": {
            "required": False,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/VerifyRequest"},
                    "examples": {
                        "nur_lesen": {
                            "summary": "1. Nur Metadaten lesen",
                            "description": "Gibt die aktuellen Repository-Metadaten zurück (kein Vergleich).",
                            "value": {},
                        },
                        "mit_vergleich": {
                            "summary": "2. SOLL/IST-Vergleich",
                            "description": "Kopiere den Output von /generate als expected_metadata rein.",
                            "value": {
                                "expected_metadata": {
                                    "contextName": "default",
                                    "schemaVersion": "1.8.1",
                                    "metadataset": "event.json",
                                    "cclom:title": "Workshop KI in der Bildung",
                                    "ccm:wwwurl": "https://example.com/workshop",
                                    "ccm:taxonid": "http://w3id.org/openeduhub/vocabs/discipline/12002",
                                },
                            },
                        },
                    },
                }
            },
        }
    },
)
async def verify_upload(node_id: NodeId, request: Request):
    """
    Verify uploaded metadata by reading it back from the repository.

    Optionally compares against expected metadata to produce a field-level diff.
    """
    require_node_id(node_id)

    repo_service = require_repository_service()

    # Parse optional body
    expected_metadata = None
    repository = "staging"
    context = "default"
    version = "latest"

    raw_body = await request.body()
    if raw_body and raw_body.strip():
        # A body means the caller asked for a SOLL/IST comparison. A broken body
        # must not degrade into a plain read that answers 200 — that would report
        # a comparison as done which never happened.
        data = parse_json_object(raw_body, '{"expected_metadata": {...}}')

        try:
            req = VerifyRequest(**data)
        except ValidationError as e:
            raise invalid_body(e) from e

        expected_metadata = req.expected_metadata

        # Extract context/version from expected metadata if available
        if expected_metadata:
            # `or`: a marker that is null means the default, as on /validate.
            context = expected_metadata.get("contextName") or "default"
            version = expected_metadata.get("schemaVersion") or "latest"

    result = await repo_service.verify_node(
        node_id=node_id,
        repository=repository,
        expected_metadata=expected_metadata,
        context=context,
        version=version,
    )

    if not result.get("success"):
        # A missing node is a 404 as on /nodes; anything else the repository's fault.
        raise HTTPException(
            status_code=SAVE_NODE_ERROR_STATUS.get(result.get("error_code"), 502),
            detail=result.get("error", "Failed to verify node"),
        )

    # Build response
    diff_list = None
    if result.get("diff"):
        diff_list = [FieldDiff(**d) for d in result["diff"]]

    return VerifyResponse(
        success=True,
        node_id=node_id,
        actual_metadata=result["actual_metadata"],
        unresolved=result.get("unresolved"),
        diff=diff_list,
        summary=result.get("summary"),
    )
