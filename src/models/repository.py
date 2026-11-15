"""Models of the repository writes: /upload, /node, /nodes, /workflow, /upload/verify."""

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from ..utils.loggable import shortened
from .common import (
    MAX_URL_LENGTH,
    ListedName,
    SchemaMarkersMixin,
    blank_is_unset,
    normalize_collection_ids,
    validate_node_id,
)
from .repository_rules import (
    DEFAULT_WORKFLOW_STATUS,
    KNOWN_WORKFLOW_STATUS,
    QUALITY_WORKFLOW_STATUS,
)
from .screenshot import ScreenshotMethod


class UploadRequest(SchemaMarkersMixin):
    """
    Request model for uploading metadata to WLO repository.

    Accepts the JSON output from /generate endpoint.
    """

    metadata: dict[str, Any] = Field(
        ...,
        description="Metadata dict from /generate endpoint (with contextName, schemaVersion, etc.)",
    )
    repository: str = Field(
        default="staging",
        deprecated=True,
        description="Deprecated — wird ignoriert. Die Repository-URL kommt aus METADATA_AGENT_REPOSITORY_URL. Wird aus Abwärtskompatibilität weiterhin akzeptiert.",
    )
    check_duplicates: bool = Field(
        default=True, description="Check for duplicates by ccm:wwwurl before uploading"
    )

    start_workflow: bool = Field(
        default=True, description="Start review workflow after upload"
    )
    return_full_node: bool = Field(
        default=False,
        description="Read the node back after writing and return it as 'node_full' (complete edu-sharing node, same shape as GET /node/v1/nodes/-home-/{id}/metadata). Costs one extra repository call.",
    )
    source: Optional[str] = Field(
        default=None,
        description="Bezugsquelle / Publisher-Name. Wenn angegeben, wird ccm:oeh_publisher_combined mit diesem Wert überschrieben.",
    )
    # Collection options
    # Always a list, never a bare string: the other list parameters of this
    # endpoint (workflow_steps, workflow_receiver) take a list and nothing else,
    # and a field that silently accepts both makes the request format depend on
    # which parameter you happen to be filling in.
    collection_id: Optional[list[ListedName]] = Field(
        default=None,
        description=(
            "Sammlung(en), in der/denen der hochgeladene Inhalt referenziert wird. "
            "Immer eine Liste — auch bei einer einzelnen Sammlung. Die Einträge "
            "sind IDs oder Sammlungs-URLs "
            "(z.B. '.../components/collections?id=<uuid>'). Sammlungen aus den "
            "Metadaten (virtual:collection_id_primary, ccm:collection_id) werden "
            "zusätzlich berücksichtigt."
        ),
    )

    # Two-step upload: create the node first (POST /node), fill it in later.
    # Without it every /upload creates its own node.
    node_id: Optional[str] = Field(
        default=None,
        description=(
            "Bestehender Node, in den geschrieben werden soll — z.B. die ID aus "
            "POST /node. Ohne Angabe wird ein neuer Node angelegt. Bei "
            "angegebener ID entfällt die Dublettenprüfung (der Node trägt die "
            "URL bereits), der Node wird bei einem Fehler **nicht** verworfen, und "
            "wie bei /nodes gilt das Zwei-Stunden-Fenster: ein älterer Node wird "
            "mit 403 abgelehnt, ein unbekannter mit 404."
        ),
    )
    _check_upload_node_id = field_validator("node_id")(validate_node_id)

    _check_collection_id = field_validator("collection_id", mode="before")(
        normalize_collection_ids
    )

    # Workflow options
    workflow_steps: Optional[list[ListedName]] = Field(
        default=None,
        description=(
            "Workflow-Status, die nach dem Upload der Reihe nach gesetzt werden. "
            "Standard: nur '200_tocheck' (Übergabe zur Prüfung durch Menschen). "
            "Beispiel für 'Qualität bestätigt': "
            "['200_tocheck', '140_ELEMENT_LEGALLY_APPROVED']. "
            f"Erlaubt: {', '.join(sorted(KNOWN_WORKFLOW_STATUS))}. "
            "Wirkt nur bei start_workflow=true."
        ),
    )
    workflow_comment: Optional[str] = Field(
        default=None,
        description="Kommentar, der bei jedem Workflow-Schritt protokolliert wird. Standard: 'Upload via Metadata Agent API'.",
    )
    workflow_receiver: Optional[list[str]] = Field(
        default=None,
        description=(
            "Authority-Namen, die bei jedem Workflow-Schritt benachrichtigt werden "
            "(z.B. ['GROUP_ORG_WLO-Uploadmanager']). Standard: die Uploadmanager-"
            "Gruppe für '200_tocheck', leer für alle anderen Status."
        ),
    )

    @field_validator("workflow_steps")
    @classmethod
    def validate_workflow_steps(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        """Reject unknown states — a typo must not end up in the repository."""
        if v is None:
            return None
        if not v:
            # Reads as "run no steps", but omitting it means "run the default",
            # which hands the node to the editorial queue — the opposite of what
            # was asked. start_workflow=false says it without the ambiguity.
            raise ValueError(
                "workflow_steps darf nicht leer sein. Für einen Upload ohne "
                "Workflow-Schritt start_workflow=false setzen."
            )
        unknown = [s for s in v if s not in KNOWN_WORKFLOW_STATUS]
        if unknown:
            raise ValueError(
                f"Unbekannte Workflow-Status: {shortened(', '.join(unknown))}. "
                f"Erlaubt: {', '.join(sorted(KNOWN_WORKFLOW_STATUS))}"
            )
        return v

    # Screenshot / Preview options
    preview_url: Optional[str] = Field(
        default=None,
        max_length=MAX_URL_LENGTH,
        description="URL for preview screenshot. If provided, a screenshot is captured async and uploaded as node preview.",
    )
    screenshot_method: ScreenshotMethod = Field(
        default=ScreenshotMethod.PLAYWRIGHT,
        description="Screenshot method: 'playwright' (local, default) or the legacy alias 'pageshot' (also captured locally)",
    )

    @field_validator("screenshot_method", mode="before")
    @classmethod
    def sanitize_screenshot_method(cls, v: Any) -> str:
        """Convert empty string to the default 'playwright'."""
        if not v or (isinstance(v, str) and not v.strip()):
            return "playwright"
        return v

    _blank_is_unset = field_validator(
        "source",
        "workflow_comment",
        "preview_url",
        "extended_text",
        mode="before",
    )(blank_is_unset)

    # Extended Data options
    write_extended_data: bool = Field(
        default=True,
        description="Write extended fields (ccm:oeh_extendedType, ccm:oeh_extendedData, ccm:oeh_extendedText) to node. Default: true.",
    )
    extended_text: Optional[str] = Field(
        default=None,
        description="Raw source text before extraction (user input, extracted page content, etc.). Written to ccm:oeh_extendedText.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "metadata": {
                        "contextName": "default",
                        "schemaVersion": "1.8.1",
                        "metadataset": "event.json",
                        "cclom:title": "Example Event",
                        "cclom:general_description": "Description...",
                        "ccm:wwwurl": "https://example.com/event",
                    },
                    "check_duplicates": True,
                    "start_workflow": True,
                    "return_full_node": False,
                    "source": "Klexikon",
                    "preview_url": "https://example.com/event",
                    "screenshot_method": "playwright",
                    "write_extended_data": True,
                    "extended_text": "Raw text content...",
                }
            ]
        }
    }


class UploadedNodeInfo(BaseModel):
    """Information about uploaded or existing node."""

    nodeId: str
    title: Optional[str] = None
    description: Optional[str] = None
    wwwurl: Optional[str] = None
    repositoryUrl: Optional[str] = None


class FieldUploadError(BaseModel):
    """Error info for a single field that failed during upload."""

    field_id: str
    error: str
    status_code: Optional[int] = None


class CollectionResult(BaseModel):
    """Result of referencing the node in a single collection."""

    collectionId: str
    success: bool
    error: Optional[str] = None


class WorkflowStepResult(BaseModel):
    """Result of a single workflow state transition."""

    status: str
    success: bool
    error: Optional[str] = None
    # True for a step that was not sent because an earlier one failed — a later
    # state presumes the earlier ones (no publishing after a failed approval).
    skipped: Optional[bool] = None


class CreateNodeRequest(BaseModel):
    """
    Request for creating a node without writing metadata to it.

    The first half of an upload on its own: a caller who needs a node id before
    the metadata exists gets one here and hands it back to /upload later.
    """

    metadata: dict[str, Any] = Field(
        ...,
        description=(
            "Mindestens 'cclom:title'. Übernommen werden außerdem "
            "'cclom:general_description', 'cclom:general_keyword', 'ccm:wwwurl' "
            "und 'cclom:general_language' — genau die Felder, die der Upload "
            "beim Anlegen setzt. Alles andere wird ignoriert und gehört in den "
            "zweiten Schritt."
        ),
    )
    check_duplicates: bool = Field(
        default=False,
        description=(
            "Vor dem Anlegen nach 'ccm:wwwurl' suchen. Standardmäßig aus, weil "
            "die URL zu diesem Zeitpunkt oft noch nicht feststeht."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "metadata": {
                        "cclom:title": "Bruchrechnung Klasse 6",
                        "ccm:wwwurl": "https://example.org/bruchrechnung",
                    },
                    "check_duplicates": False,
                }
            ]
        }
    }


class CreateNodeResponse(BaseModel):
    """Response from node creation."""

    success: bool
    duplicate: Optional[bool] = None
    node: Optional[UploadedNodeInfo] = None
    error: Optional[str] = None


class UploadResponse(BaseModel):
    """Response from repository upload."""

    success: bool
    duplicate: Optional[bool] = None
    node: Optional[UploadedNodeInfo] = None
    node_full: Optional[dict[str, Any]] = Field(
        default=None,
        description="Complete edu-sharing node as returned by the repository (same shape as the 'node' object of GET /node/v1/nodes/-home-/{id}/metadata). Only present when 'return_full_node' was requested and the read succeeded.",
    )
    error: Optional[str] = None
    step: Optional[str] = None
    fields_written: Optional[int] = None
    fields_skipped: Optional[int] = None
    skipped_fields: Optional[list[str]] = Field(
        default=None,
        description=(
            "Namen der gesendeten Felder, die auf keine Repository-Property "
            "geschrieben wurden (in fields_skipped gezählt)."
        ),
    )
    node_created: Optional[bool] = Field(
        default=None,
        description=(
            "true, wenn dieser Aufruf den Node angelegt hat; false, wenn er über "
            "'node_id' übergeben wurde. Beide Fälle antworten mit 200 und "
            "derselben nodeId — sonst unterscheidet sie nichts."
        ),
    )
    schema_used: Optional[str] = Field(
        default=None,
        description=(
            "Typschema, gegen das geschrieben wurde (aus 'metadataset'). "
            "null bedeutet: nur core.json galt — wurde kein metadataset "
            "mitgeschickt, fallen typspezifische Felder weg."
        ),
    )
    repo_fields_available: Optional[int] = Field(
        default=None,
        description=(
            "Anzahl der Felder, die dieses Schema überhaupt ins Repository "
            "schreiben darf. Deutlich weniger als erwartet heißt: falsches "
            "oder fehlendes 'metadataset'."
        ),
    )
    field_errors: Optional[list[FieldUploadError]] = None
    preview: Optional[dict[str, Any]] = Field(
        default=None,
        description="Preview screenshot status: {success, method, capture_time_ms, size_bytes} or {success: false, error}",
    )
    collections: Optional[list[CollectionResult]] = Field(
        default=None,
        description="Ergebnis pro Sammlung, in der der Node referenziert wurde. Nur vorhanden, wenn Sammlungen angegeben waren.",
    )
    workflow: Optional[list[WorkflowStepResult]] = Field(
        default=None,
        description="Ergebnis pro Workflow-Schritt, in der ausgeführten Reihenfolge. Nur vorhanden, wenn start_workflow=true war.",
    )
    discarded_node: Optional[str] = Field(
        default=None,
        description="ID des unvollständigen Nodes, der nach einem Abbruch zurückgenommen wurde (Papierkorb, wiederherstellbar). Nur nach einem fehlgeschlagenen Upload gesetzt.",
    )


class SaveNodeRequest(SchemaMarkersMixin):
    """
    Request for writing metadata to a node — creating it or updating one.

    Same body as an upload, minus everything that only makes sense for a
    first-time upload: no duplicate check, no screenshot, no choice about
    reading the node back. What distinguishes it is `node_id`.
    """

    metadata: dict[str, Any] = Field(
        ...,
        description="Metadata dict from /generate endpoint (with contextName, schemaVersion, etc.)",
    )

    node_id: Optional[str] = Field(
        default=None,
        description=(
            "Node, der aktualisiert werden soll. Ohne Angabe wird ein neuer Node "
            "angelegt. Felder, die im Body fehlen, bleiben am Node unverändert. "
            "Ein Node, der älter als 2 Stunden ist, wird mit 403 abgelehnt."
        ),
    )
    _check_save_node_id = field_validator("node_id")(validate_node_id)

    collection_id: Optional[list[ListedName]] = Field(
        default=None,
        description=(
            "Sammlung(en), in der/denen der Inhalt referenziert wird. Immer eine "
            "Liste — auch bei einer einzelnen Sammlung. Die Einträge sind IDs "
            "oder Sammlungs-URLs (z.B. '.../components/collections?id=<uuid>'). "
            "Sammlungen aus den Metadaten (virtual:collection_id_primary, "
            "ccm:collection_id) werden zusätzlich berücksichtigt."
        ),
    )

    _check_collection_id = field_validator("collection_id", mode="before")(
        normalize_collection_ids
    )

    write_extended_data: bool = Field(
        default=False,
        description=(
            "Extended-Felder schreiben (ccm:oeh_extendedType, "
            "ccm:oeh_extendedData, ccm:oeh_extendedText). Standard: false — "
            "ccm:oeh_extendedData wird als Ganzes ersetzt, ein Teil-Update "
            "würde die weggelassenen Felder darin also verlieren."
        ),
    )
    extended_text: Optional[str] = Field(
        default=None,
        description="Rohtext vor der Extraktion. Wird in ccm:oeh_extendedText geschrieben. Wirkt nur bei write_extended_data=true.",
    )

    # One parameter for both forms: callers that already hold the image (a
    # captured screenshot, an upload from a form) would otherwise have to put it
    # somewhere reachable first, and callers that only have the address of the
    # page's hero image would have to fetch it themselves.
    preview: Optional[str] = Field(
        default=None,
        description=(
            "Vorschaubild des Nodes. Entweder eine http(s)-URL, die der Dienst "
            "lädt, oder das Bild selbst als Data-URL "
            "('data:image/png;base64,…') bzw. nacktes Base64. Erlaubt sind PNG, "
            "JPEG, GIF und WebP bis 10 MB (SVG nicht). Ohne Angabe bleibt ein "
            "vorhandenes Vorschaubild unverändert."
        ),
    )

    # Two separate switches rather than one list of states: each names a
    # decision the caller makes about this content, and both are off by default
    # so nothing enters the editorial queue unasked.
    start_review_workflow: bool = Field(
        default=False,
        description=(
            f"Node zur Prüfung übergeben ('{DEFAULT_WORKFLOW_STATUS}', Empfänger: "
            "die WLO-Uploadmanager-Gruppe). Standard: false."
        ),
    )
    start_quality_workflow: bool = Field(
        default=False,
        description=(
            f"Qualität bestätigen ('{QUALITY_WORKFLOW_STATUS}'). Standard: false. "
            "Zusammen mit start_review_workflow werden beide Status nacheinander "
            "gesetzt, jeder als eigener Eintrag in der Workflow-Historie."
        ),
    )

    _blank_is_unset = field_validator("extended_text", "preview", mode="before")(
        blank_is_unset
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "metadata": {
                        "contextName": "default",
                        "schemaVersion": "2.0.0",
                        "metadataset": "learning_material.json",
                        "cclom:title": "Kernphysik",
                        "ccm:wwwurl": "https://de.wikipedia.org/wiki/Kernphysik",
                    },
                    "node_id": None,
                    "write_extended_data": False,
                    "start_review_workflow": False,
                    "start_quality_workflow": False,
                }
            ]
        }
    }


class SaveNodeResponse(BaseModel):
    """Response from writing a node — the full node is always included."""

    success: bool
    node: Optional[UploadedNodeInfo] = None
    node_full: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Vollständiger edu-sharing Node, wie ihn das Repository liefert "
            "(gleiche Struktur wie das 'node'-Objekt von "
            "GET /node/v1/nodes/-home-/{id}/metadata). Leer nur, wenn das "
            "Zurücklesen fehlgeschlagen ist — der Schreibvorgang gilt dann "
            "weiterhin als erfolgreich."
        ),
    )
    node_created: Optional[bool] = Field(
        default=None,
        description=(
            "true, wenn dieser Aufruf den Node angelegt hat; false bei einem "
            "Update über 'node_id'."
        ),
    )
    error: Optional[str] = None
    step: Optional[str] = None
    fields_written: Optional[int] = None
    fields_skipped: Optional[int] = None
    skipped_fields: Optional[list[str]] = Field(
        default=None,
        description=(
            "Namen der gesendeten Felder, die auf keine Repository-Property "
            "geschrieben wurden (in fields_skipped gezählt)."
        ),
    )
    schema_used: Optional[str] = Field(
        default=None,
        description=(
            "Typschema, gegen das geschrieben wurde (aus 'metadataset'). "
            "null bedeutet: nur core.json galt — wurde kein metadataset "
            "mitgeschickt, fallen typspezifische Felder weg."
        ),
    )
    repo_fields_available: Optional[int] = Field(
        default=None,
        description=(
            "Anzahl der Felder, die dieses Schema überhaupt ins Repository "
            "schreiben darf. Deutlich weniger als erwartet heißt: falsches "
            "oder fehlendes 'metadataset'."
        ),
    )
    field_errors: Optional[list[FieldUploadError]] = None
    preview: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Ergebnis des Vorschaubilds: {success, mimetype, size_bytes} bzw. "
            "{success: false, error}. Nur vorhanden, wenn 'preview' angegeben "
            "war. Ein gescheitertes Vorschaubild macht das Schreiben der "
            "Metadaten nicht ungültig, taucht aber zusätzlich in 'error' auf."
        ),
    )
    collections: Optional[list[CollectionResult]] = Field(
        default=None,
        description="Ergebnis pro Sammlung, in der der Node referenziert wurde. Nur vorhanden, wenn Sammlungen angegeben waren.",
    )
    workflow: Optional[list[WorkflowStepResult]] = Field(
        default=None,
        description="Ergebnis pro Workflow-Schritt, in der ausgeführten Reihenfolge. Nur vorhanden, wenn ein Workflow angefordert war.",
    )
    discarded_node: Optional[str] = Field(
        default=None,
        description="ID des unvollständigen Nodes, der nach einem Abbruch zurückgenommen wurde (Papierkorb, wiederherstellbar). Nur nach einem fehlgeschlagenen Anlegen gesetzt.",
    )


class WorkflowRequest(BaseModel):
    """Request for advancing an existing node through the review workflow."""

    steps: list[ListedName] = Field(
        ...,
        min_length=1,
        description=(
            "Workflow-Status, die der Reihe nach gesetzt werden. Jeder Schritt "
            "erzeugt einen eigenen Eintrag in der Workflow-Historie des Nodes — "
            "so bleibt nachvollziehbar, wer welchen Status gesetzt hat. "
            f"Erlaubt: {', '.join(sorted(KNOWN_WORKFLOW_STATUS))}."
        ),
    )
    comment: Optional[str] = Field(
        default=None,
        description="Kommentar, der bei jedem Schritt protokolliert wird. Standard: 'Upload via Metadata Agent API'.",
    )
    receiver: Optional[list[str]] = Field(
        default=None,
        description=(
            "Authority-Namen, die benachrichtigt werden (z.B. ['GROUP_ORG_WLO-Uploadmanager']). "
            "Standard: die Uploadmanager-Gruppe für '200_tocheck', leer für alle anderen Status."
        ),
    )

    _blank_is_unset = field_validator("comment", mode="before")(blank_is_unset)

    @field_validator("steps", mode="before")
    @classmethod
    def normalize_steps(cls, v: Any) -> Any:
        """Accept a single status string as well as a list."""
        if isinstance(v, str):
            return [v]
        return v

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v: list[str]) -> list[str]:
        """Reject unknown states — a typo must not end up in the repository."""
        unknown = [s for s in v if s not in KNOWN_WORKFLOW_STATUS]
        if unknown:
            raise ValueError(
                f"Unbekannte Workflow-Status: {shortened(', '.join(unknown))}. "
                f"Erlaubt: {', '.join(sorted(KNOWN_WORKFLOW_STATUS))}"
            )
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "steps": ["140_ELEMENT_LEGALLY_APPROVED"],
                    "comment": "",
                }
            ]
        }
    }


class WorkflowResponse(BaseModel):
    """Response after running workflow steps on a node."""

    success: bool
    nodeId: str
    steps: Optional[list[WorkflowStepResult]] = Field(
        default=None,
        description="Ergebnis pro Schritt, in der ausgeführten Reihenfolge",
    )
    current_status: Optional[str] = Field(
        default=None,
        description="ccm:wf_status des Nodes nach den Schritten (zurückgelesen aus dem Repository)",
    )
    history: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Workflow-Historie des Nodes — enthält pro Eintrag u.a. Status, Kommentar, Zeitpunkt und den ausführenden Nutzer.",
    )
    repositoryUrl: Optional[str] = None
    error: Optional[str] = None


class FieldDiff(BaseModel):
    """Diff info for a single metadata field."""

    field_id: str
    status: (
        str  # "match", "mismatch", "missing_in_repo", "extra_in_repo", "not_written"
    )
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    resolution: Optional[str] = Field(
        default=None,
        description=(
            "`resolved`, `unresolved` oder null. Unabhängig von `status`: ein "
            "Feld kann exakt den gesendeten Wert tragen (`match`) und trotzdem "
            "auf nichts auflösen. null heißt: nichts aufzulösen (Freitext) oder "
            "nicht geprüft."
        ),
    )


class VerifyRequest(SchemaMarkersMixin):
    """Request for verifying uploaded metadata against repository."""

    expected_metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Expected metadata (e.g. output from /generate). If provided, a SOLL/IST diff is computed.",
    )

    repository: str = Field(
        default="staging",
        deprecated=True,
        description="Deprecated — wird ignoriert. Die Repository-URL kommt aus METADATA_AGENT_REPOSITORY_URL. Wird aus Abwärtskompatibilität weiterhin akzeptiert.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "expected_metadata": {
                        "cclom:title": "Example Event",
                        "cclom:general_description": "Description...",
                        "ccm:wwwurl": "https://example.com/event",
                        "ccm:taxonid": "http://w3id.org/openeduhub/vocabs/discipline/12002",
                    },
                }
            ]
        }
    }


class VerifyResponse(BaseModel):
    """Response from upload verification."""

    success: bool
    node_id: str
    actual_metadata: dict[str, Any] = Field(
        description="Flat metadata as read from the repository"
    )
    unresolved: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description=(
            "Werte, die im Feld stehen, die das Repository aber nicht als Wert "
            "liest — leeres `_DISPLAYNAME` bzw. fehlendes `virtual:licenseurl`. "
            "Ohne `expected_metadata` ermittelt: ein Feld kann exakt das "
            "enthalten, was gesendet wurde, und trotzdem auf nichts auflösen. "
            "Leere Liste = alles löst auf."
        ),
    )
    diff: Optional[list[FieldDiff]] = Field(
        default=None,
        description="Field-level diff (only when expected_metadata is provided)",
    )
    summary: Optional[dict[str, int]] = Field(
        default=None,
        description="Diff summary: match, mismatch, missing_in_repo, extra_in_repo, not_written counts",
    )
    error: Optional[str] = None
