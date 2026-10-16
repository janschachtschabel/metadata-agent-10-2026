"""FastAPI application for metadata extraction."""

import json
import mimetypes
import time
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import get_swagger_ui_html
from pydantic import BaseModel, ValidationError
from contextlib import asynccontextmanager

from .config import get_settings
from .models.schemas import (
    GenerateRequest,
    GenerateResponse,
    ValidateRequest,
    ValidateResponse,
    ExportMarkdownRequest,
    ExportMarkdownResponse,
    SchemataInfoResponse,
    ContextInfo,
    SchemaInfo,
    CreateNodeRequest,
    CreateNodeResponse,
    SaveNodeRequest,
    SaveNodeResponse,
    UploadRequest,
    UploadResponse,
    UploadedNodeInfo,
    FieldUploadError,
    CollectionResult,
    WorkflowStepResult,
    WorkflowRequest,
    WorkflowResponse,
    VerifyRequest,
    VerifyResponse,
    FieldDiff,
    DetectContentTypeRequest,
    DetectContentTypeResponse,
    ContentTypeInfo,
    LocalizedString,
    ExtractFieldRequest,
    ExtractFieldResponse,
    InputSource,
    ScreenshotRequest,
    ScreenshotResponse,
)
from .services.diagnostics import check_dependencies
from .services.input_source_service import TextExtractionError, get_input_source_service
from .utils.public_url import PublicUrlError
from .utils.request_id import HEADER as REQUEST_ID_HEADER
from .utils.request_id import RequestIdMiddleware, current_request_id
from .utils.body_limit import BodySizeLimitMiddleware
from .utils.logging_setup import configure_logging
from .infra import (
    InternalPortUnavailable,
    create_internal_app,
    health_payload,
    readiness_response,
    start_internal_server,
)
from .metrics import GENERATIONS, OWN_SERVICE_HOOKS, HttpMetricsMiddleware
from .services.metadata_service import get_metadata_service
from .services.repository_curation import (
    DEFAULT_WORKFLOW_STATUS,
    QUALITY_WORKFLOW_STATUS,
    is_valid_node_id,
)
from .services.repository_service import get_repository_service
from .services.llm_auth import (
    ForwardedAuthorizationMiddleware,
    current_auth_source,
    mask_header,
)
from .services.llm_errors import LLMError, http_status_for
from .services.llm_service import get_llm_service
from .services.field_normalizer import get_field_normalizer
from .services.screenshot_service import (
    LOCAL_METHODS,
    ScreenshotError,
    get_screenshot_service,
)
from .utils.schema_loader import (
    get_available_contexts,
    get_available_schemas,
    load_schema,
    get_content_types,
    get_latest_version,
    resolve_schema_file_or_uri,
    get_content_type_uri,
)
import logging

logger = logging.getLogger(__name__)


def carry_schema_markers(data: dict) -> dict:
    """
    Move contextName, schemaVersion and metadataset into `metadata`.

    /generate answers with those markers *beside* `metadata`, while every
    consumer looks for them *inside* it. Posting that response verbatim — the
    way the docs describe — would otherwise fall back to auto-detection:
    /upload writes only the core.json fields and silently drops the
    type-specific ones, /validate and /export/markdown work against a schema
    they guessed. Markers already present inside win, which is what the web
    component sends.

    Mutates and returns `data`. The markers are filtered out again before
    anything reaches the repository.
    """
    inner = data.get("metadata")
    if not isinstance(inner, dict):
        return data
    for marker in ("contextName", "schemaVersion", "metadataset"):
        if marker in data and marker not in inner:
            inner[marker] = data[marker]
    return data


def sanitize_json_string(raw_body: str) -> str:
    """
    Sanitize raw JSON string by escaping control characters in string values.
    This allows malformed JSON with literal newlines in strings to be parsed.
    Handles multi-line text input that wasn't properly escaped.
    """
    # Remove BOM if present
    raw_body = raw_body.lstrip("\ufeff")

    # First, try to parse as-is
    try:
        json.loads(raw_body)
        return raw_body  # Already valid JSON
    except json.JSONDecodeError:
        pass

    # Escape literal newlines and tabs inside JSON strings
    # This handles cases where users paste multi-line text without escaping
    result = []
    in_string = False
    escape_next = False

    for char in raw_body:
        if escape_next:
            result.append(char)
            escape_next = False
            continue

        if char == "\\":
            result.append(char)
            escape_next = True
            continue

        if char == '"':
            in_string = not in_string
            result.append(char)
            continue

        if in_string:
            # Escape control characters inside strings
            if char == "\n":
                result.append("\\n")
            elif char == "\r":
                result.append("\\r")
            elif char == "\t":
                result.append("\\t")
            elif ord(char) < 32:
                # Remove other control characters
                pass
            else:
                result.append(char)
        else:
            result.append(char)

    return "".join(result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup - with the module's settings, the same the endpoints answer with.
    llm_config = settings.get_llm_config()
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"LLM Provider: {settings.llm_provider}")
    logger.info(f"LLM Model: {llm_config['model']}")
    logger.info(f"LLM API Base: {llm_config['api_base']}")
    logger.info(f"B-API Base URL: {settings.b_api_base_url}")
    logger.info(
        f"B-API Auth Mode: {settings.b_api_auth_mode} "
        f"(B_API_KEY {'gesetzt' if settings.b_api_key else 'nicht gesetzt'})"
    )
    logger.info(f"Text Extraction URL: {settings.text_extraction_api_url}")
    logger.info(f"Repository URL: {settings.repository_url}")
    for warning in settings.config_warnings():
        logger.warning(f"⚠️ {warning}")

    # The gateway budget decides the real parallelism — printing the worker
    # default alone would say 20 where AcademicCloud runs 2.
    concurrent = llm_config["max_concurrent_requests"]
    rate = llm_config["requests_per_second"]
    effective = min(settings.default_max_workers, concurrent)
    logger.info(
        f"Default Workers: {settings.default_max_workers}"
        + (
            f" → {effective} (limit of {llm_config['limit_group']})"
            if effective < settings.default_max_workers
            else ""
        )
    )
    logger.info(
        f"LLM Throughput: max {concurrent} in flight, "
        + (f"max {rate:g} req/s" if rate else "no rate cap")
    )

    # /health, /ready, /metrics on a port the ingress does not expose.
    stop_internal_server = None
    if settings.internal_port:
        try:
            stop_internal_server = await start_internal_server(
                create_internal_app(get_internal_settings), settings.internal_port
            )
        except InternalPortUnavailable as e:
            # The API itself keeps running; probes on the internal port fail.
            logger.error(f"❌ {e} Der Dienst läuft ohne internen Port weiter.")
        else:
            logger.info(
                f"Internal port: {settings.internal_port} (/health, /ready, /metrics)"
            )

    yield

    if stop_internal_server:
        await stop_internal_server()

    # Shutdown - close HTTP clients
    from .services.llm_service import _llm_service

    if _llm_service:
        await _llm_service.close()
    from .services.metadata_service import _metadata_service

    if _metadata_service and _metadata_service.llm_service:
        await _metadata_service.llm_service.close()
    from .services.input_source_service import _input_source_service

    if _input_source_service:
        await _input_source_service.close()
    logger.info("Shutting down...")


# Create FastAPI app
settings = get_settings()


def get_internal_settings():
    """The settings the internal port reports on - the same as the app's."""
    return settings


# stdout, text or JSON lines (METADATA_AGENT_LOG_FORMAT / _LOG_LEVEL)
configure_logging(settings.log_level, settings.log_format)
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    # For end users and the B-API product catalogue (OpenAPI info.summary).
    summary=(
        "Erschließt Bildungsinhalte mit KI: Aus einem Text, einer Webseite oder "
        "einem Repository-Eintrag erzeugt der Dienst passende Metadaten wie Titel, "
        "Beschreibung, Schlagworte, Bildungsstufe und Inhaltsart."
    ),
    description="""API für die automatische Extraktion von Metadaten aus Texten mittels KI.

## Features

- **Automatische Schema-Erkennung**: Erkennt den passenden Inhaltstyp
- **Parallele Verarbeitung**: Bis zu 20 Worker für schnelle Extraktion
- **Mehrsprachig**: Deutsch und Englisch
- **Validierung**: Prüft extrahierte Metadaten gegen Schema
- **Markdown-Export**: Menschenlesbare Ausgabe

## Authentifizierung

Die API selbst prüft keine Zugangsdaten. Sie ist für den Betrieb hinter einem
vorgeschalteten Proxy gedacht, der den Zugang absichert (in edu-sharing der
bapi-Proxy). Lese- und Schreibzugriffe auf das Repository laufen unter dem
konfigurierten Service-Account (`WLO_GUEST_USERNAME`).
    """,
    lifespan=lifespan,
    # The default pages load Swagger UI from cdn.jsdelivr.net (and /redoc also
    # Google Fonts). /docs is served below from src/static/docs instead.
    docs_url=None,
    redoc_url=None,
)


# Custom OpenAPI schema: inject request models that are referenced via $ref
# but not auto-registered because endpoints use raw Request instead of Pydantic params
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        summary=app.summary,
        description=app.description,
        routes=app.routes,
    )
    # Inject missing request schemas
    schemas = openapi_schema.setdefault("components", {}).setdefault("schemas", {})
    for model in [
        GenerateRequest,
        DetectContentTypeRequest,
        ExtractFieldRequest,
        ValidateRequest,
        ExportMarkdownRequest,
        UploadRequest,
        VerifyRequest,
        WorkflowRequest,
    ]:
        model_schema = model.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        # Extract $defs (sub-schemas like enums) and merge into top-level schemas
        defs = model_schema.pop("$defs", {})
        for def_name, def_schema in defs.items():
            schemas.setdefault(def_name, def_schema)
        schemas[model.__name__] = model_schema
    app.openapi_schema = openapi_schema
    return openapi_schema


app.openapi = custom_openapi


def documented_body_schema(model: type[BaseModel]) -> dict:
    """
    Describe the body of an endpoint that takes a raw `Request`.

    /upload accepts two shapes — the fields flat at the top level, which is the
    documented recommendation, or wrapped under `metadata` — so no single
    Pydantic parameter can express it and the route has to declare its body via
    `openapi_extra`. That slot held `{"type": "object"}`: an object with no
    properties, so the Schema tab in /docs listed nothing and every option was
    invisible unless it happened to appear in an example. Writing the properties
    out by hand instead would put a second copy of every option next to the
    model, to drift on the next one added.

    Two relaxations against the model, both because of the flat form: nothing is
    required (its `metadata` is spread across the top level), and the extra
    properties are those metadata fields.
    """
    schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
    # custom_openapi() puts the same sub-schemas into components, which is where
    # the refs left in `properties` point.
    schema.pop("$defs", None)
    schema.pop("required", None)
    # The route offers named examples; the model's own would compete with them.
    schema.pop("examples", None)
    schema["additionalProperties"] = True
    return schema


# CORS middleware - origins configurable via METADATA_AGENT_CORS_ORIGINS env var
_cors_origins = (
    [o.strip() for o in settings.cors_origins.split(",")]
    if settings.cors_origins != "*"
    else ["*"]
)
# Bound request bodies before an endpoint reads them into memory. Registered
# first, so CORS, metrics and the request id all wrap its 413.
app.add_middleware(
    BodySizeLimitMiddleware, max_bytes=lambda: settings.max_request_bytes
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # Must stay False while allow_origins can be "*" — browsers reject the
    # wildcard origin in combination with credentials.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Lets an embedding page on another origin read the id of a failed call.
    expose_headers=[REQUEST_ID_HEADER],
)

# Gzip compression for all responses >= 500 bytes (widget JS/CSS + API JSON)
app.add_middleware(GZipMiddleware, minimum_size=500)

# The B-API proxy forwards the caller's key as `Authorization: Bearer`; LLM calls
# of this request pass it on (METADATA_AGENT_B_API_AUTH_MODE, llm_auth.py).
app.add_middleware(ForwardedAuthorizationMiddleware)

# Requests by route template, for /metrics on the internal port.
app.add_middleware(HttpMetricsMiddleware)


# Outermost, so every response - errors included - carries X-Request-ID.
app.add_middleware(RequestIdMiddleware)


def check_provider_override(provider: str | None) -> None:
    """
    Refuse a request for native OpenAI on a service that runs on the B-API.

    Native OpenAI is billed to the server's OPENAI_API_KEY and receives the
    caller's text directly; on a B-API deployment that would also bypass
    B_API_AUTH_MODE=forward. It stays available where it is what the operator
    configured.
    """
    if provider == "openai" and settings.llm_provider != "openai":
        raise HTTPException(
            status_code=422,
            detail=(
                "llm_provider 'openai' ist nur erlaubt, wenn der Dienst selbst mit "
                f"Provider openai läuft (hier: {settings.llm_provider})."
            ),
        )


def parse_json_object(raw_body: bytes, example: str) -> dict:
    """
    Parse a request body that has to be a JSON object, or answer 400.

    Invalid UTF-8, invalid JSON and a valid JSON value of the wrong kind (a
    list, a number) are all the caller's mistake. Left to the endpoint they
    surfaced as 500s from `"metadata" in data` or `data.pop(...)`.
    """
    try:
        data = json.loads(sanitize_json_string(raw_body.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"Ungültiger JSON-Body: {e}") from e
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=400, detail=f"Body muss ein JSON-Objekt sein, z.B. {example}."
        )
    return data


def require_repository_service():
    """The repository service, or a 503 when its credentials are not configured."""
    repo_service = get_repository_service()
    if not repo_service:
        raise HTTPException(
            status_code=503,
            detail="Repository service not configured. Set WLO_GUEST_USERNAME and WLO_GUEST_PASSWORD environment variables.",
        )
    return repo_service


def failure_response(
    status_code: int, detail: str, error: dict, **extra
) -> JSONResponse:
    """
    An error as the API reports it: a readable `detail`, as FastAPI clients
    expect it, and `error` with the code and the request id for programs and
    support. One log line under the same id; the technical cause stays there.
    """
    error = {**error, "request_id": current_request_id()}
    context = ", ".join(
        f"{key}={error[key]}"
        for key in ("upstream_status", "auth_source")
        if error.get(key)
    )
    message = str(error.get("message") or detail).replace("\n", " ")
    logger.error(
        f"❌ [{error['request_id']}] {status_code} {error.get('code')}: {message}"
        + (f" ({context})" if context else "")
    )
    return JSONResponse(
        status_code=status_code, content={"detail": detail, "error": error, **extra}
    )


def llm_error_response(error: LLMError, **extra) -> JSONResponse:
    """
    An LLM failure. Upstream auth failures become 502 on purpose - a 401/403
    from the agent would make edu-sharing treat the user's own session as
    expired.
    """
    return failure_response(error.http_status, error.message, error.to_dict(), **extra)


def text_extraction_error_response(error: TextExtractionError) -> JSONResponse:
    """A failed URL text extraction with its named cause (504 timeout, 502 other)."""
    return failure_response(error.http_status, error.message, error.to_dict())


def generation_failed_response(result: dict) -> JSONResponse:
    """A /generate run without a single usable field - never a 200."""
    processing = result.get("processing") or {}
    error = processing.get("error") or {"code": "GENERATION_FAILED", "message": ""}
    return failure_response(
        http_status_for(error.get("code")),
        f"KI-Metadatengenerierung fehlgeschlagen: {error.get('message')}",
        error,
        contextName=result.get("contextName"),
        schemaVersion=result.get("schemaVersion"),
        metadataset=result.get("metadataset"),
        language=result.get("language"),
        processing=processing,
    )


# ============================================================================
# Widget Static Files
# ============================================================================

# Self-hosted widget fonts. Slim images ship no /etc/mime.types, which would
# leave .woff2 without its standard type (RFC 8081).
mimetypes.add_type("font/woff2", ".woff2")

# Swagger UI from the agent itself — no CDN, no third-party request when the
# docs are opened (version and checksum: src/static/docs/README.md).
_docs_assets = Path(__file__).parent / "static" / "docs"
app.mount("/docs-assets", StaticFiles(directory=str(_docs_assets)), name="docs-assets")


@app.get("/docs", include_in_schema=False)
async def swagger_ui(request: Request):
    # root_path: behind the B-API proxy the page lives under a path prefix.
    root = request.scope.get("root_path", "").rstrip("/")
    return get_swagger_ui_html(
        openapi_url=f"{root}{app.openapi_url}",
        title=f"{app.title} – Swagger UI",
        swagger_js_url=f"{root}/docs-assets/swagger-ui-bundle.js",
        swagger_css_url=f"{root}/docs-assets/swagger-ui.css",
        swagger_favicon_url=f"{root}/docs-assets/favicon-32x32.png",
        # Otherwise Swagger UI sends the spec to validator.swagger.io.
        swagger_ui_parameters={"validatorUrl": None},
    )


# Mount widget dist files if they exist
_widget_dir = Path(__file__).parent / "static" / "widget"
if _widget_dir.exists():
    app.mount(
        "/widget/examples",
        StaticFiles(directory=str(_widget_dir / "examples"), html=True),
        name="widget-examples",
    ) if (_widget_dir / "examples").exists() else None
    if (_widget_dir / "dist").exists():
        app.mount(
            "/widget/dist",
            StaticFiles(directory=str(_widget_dir / "dist")),
            name="widget-dist",
        )


WIDGET_LANGUAGES = {"de", "en"}


@app.get(
    "/widget/i18n/{lang}.json",
    summary="Widget UI-Übersetzungen",
    description="Liefert die UI-Übersetzungstexte für die Webkomponente. Unterstützte Sprachen: `de`, `en`.",
    tags=["Widget"],
)
async def widget_i18n(lang: str):
    """Serve i18n translation files for the web component."""
    i18n_dir = Path(__file__).parent / "static" / "widget" / "assets" / "i18n"
    # Allow-list, not a path check: `lang` becomes part of a file path.
    if lang not in WIDGET_LANGUAGES:
        raise HTTPException(
            status_code=404, detail=f"Language '{lang}' not found. Available: de, en"
        )
    file_path = i18n_dir / f"{lang}.json"
    if not file_path.exists():
        raise HTTPException(
            status_code=404, detail=f"Language '{lang}' not found. Available: de, en"
        )
    return JSONResponse(
        content=json.loads(file_path.read_text(encoding="utf-8")),
        headers={"Cache-Control": "public, max-age=3600"},
    )


# Layouts, attributes and events below mirror the shipped widget bundle.
# tests/test_widget_info.py verifies them against src/static/widget/dist/main.js —
# keep both in sync when the widget is rebuilt.
_WIDGET_LAYOUTS = {
    "default": "Vollständige Bearbeitung — Eingabe, Statusbar, Floating Controls, Footer.",
    "plugin": "Kompakte Sidebar für Browser-Extensions — Eingabe, Fortschritt, randlos.",
    "dialog": "Review-Dialog für Modals — kein Eingabebereich, Speichern-Button.",
    "detail": "Mehrspaltige Nur-Lese-Vorschau (4 Spalten), randlos.",
    "clean": "Minimale randlose Ansicht zum Prüfen — editierbar, kompakt.",
    "prueftisch": "Prüftabelle mit gruppierten Karten, 1-spaltig.",
    "prueftisch-org": "Organisatorischer Prüftisch — randlos, nur lesend.",
}

# Aliases resolve to a layout; they do NOT change any other attribute.
_WIDGET_LAYOUT_ALIASES = {
    "default": [
        "standalone",
        "local",
        "normal",
        "edit",
        "bookmarklet",
        "webcomponent",
        "embed",
        "embedded",
        "viewer",
        "view",
        "readonly",
    ],
    "plugin": ["browser-extension", "extension", "sidebar"],
    "dialog": ["modal", "review", "redaktion"],
    "detail": ["preview", "print"],
    "clean": ["metadatenpruefdialog", "pruefung", "validation", "check"],
    "prueftisch": [
        "reviewtable",
        "table",
        "qa",
        "prueftisch-gross",
        "prueftisch-large",
        "reviewtable-large",
        "qa-large",
    ],
    "prueftisch-org": [
        "reviewtable-org",
        "qa-org",
        "prueftisch-org-large",
        "reviewtable-org-large",
        "qa-org-large",
    ],
}

_WIDGET_ATTRIBUTES = {
    # Configuration
    "api-url": "Optionale URL bzw. Proxy-Basis der Metadata Agent API; Standard ist die Herkunft von main.js (alles vor /widget/dist/), sonst Same-Origin.",
    "layout": "Layout-Variante, siehe 'layouts'. Aliase siehe 'layout_aliases'.",
    "context-name": "Schema-Kontext: 'default' oder 'mds_oeh'.",
    "schema-version": "Schema-Version, z.B. '1.8.1' oder 'latest'.",
    "columns": "Spaltenanzahl 1–4. Werte außerhalb werden ignoriert.",
    "background-color": "CSS-Hintergrundfarbe.",
    "input-mode": "Eingabemodus: 'text', 'url' oder 'nodeId'.",
    "instance-id": "Instanz-Kennung. Gleiche ID = geteilter State, Events feuern einmal.",
    "debug": "Konsolen-Logging aktivieren.",
    # Visibility — opt-in: only true/"true" enables, layout default otherwise
    "show-input-area": "Eingabebereich anzeigen (opt-in).",
    "show-status-bar": "Statusleiste mit Fortschritt anzeigen (opt-in).",
    "show-footer": "Fußzeile anzeigen (opt-in).",
    "show-floating-controls": "Floating Controls anzeigen (opt-in).",
    "controls": "Alias für show-floating-controls (OEH-Kompatibilität).",
    "show-upload-button": "Upload-Button anzeigen (opt-in).",
    "show-save-button": "JSON-Download-Button anzeigen (opt-in).",
    "show-json-loader": "JSON-Import-Button anzeigen (opt-in).",
    "show-language-switcher": "Sprachumschalter de/en anzeigen (opt-in).",
    "show-content-type": "Content-Type-Selector anzeigen (opt-in).",
    "show-reset-button": "Reset-Button anzeigen (opt-in).",
    "show-page-mode": "Seitenmodus-Umschalter anzeigen (opt-in).",
    "show-content-type-only": "Nur den Content-Type-Selector anzeigen (opt-in).",
    # Visibility — opt-out: only false/"false" disables
    "show-core-fields": "Kernfelder anzeigen (opt-out).",
    "show-special-fields": "Spezialfelder anzeigen (opt-out).",
    "show-field-actions": "Feld-Aktionsbuttons anzeigen (opt-out).",
    "show-preview": "Vorschaubild anzeigen (opt-out, Standard true).",
    # Behaviour
    "readonly": "Nur-Lese-Modus.",
    "viewer-mode": "Alias für readonly (Rückwärtskompatibilität).",
    "borderless": "Rahmenloser Modus.",
    "flat-groups": "Feldgruppen pro Schema zusammenfassen.",
    "highlight-ai": "KI-generierte Felder farblich hervorheben.",
    "enable-screenshot": "Screenshot bei URL-Extraktion (opt-out).",
    "screenshot-method": "'playwright' (Standard) oder der Legacy-Alias 'pageshot' — beide nehmen lokal auf. Andere Werte werden ignoriert.",
    "auto-extract": "Nach dem Laden sofort extrahieren.",
    "force-reset": "Reset ohne Bestätigungsdialog.",
    # Data
    "text": "Text als Eingabe setzen.",
    "url": "URL als Eingabe setzen.",
    "content-type": "Inhaltstyp per Schema-Datei ('event.json') oder Vokabular-URI.",
    "metadata-input": "Vorbefüllte Metadaten. Nur per JS: canvas.metadataInput = {...}",
    "preview-image": "Vorschaubild als Data-URL oder URL. Nur per JS.",
}

_WIDGET_EVENTS = {
    "metadataChange": "Nach jeder Feldänderung (50 ms entprellt). event.detail enthält die vollständigen Metadaten.",
    "metadataSubmit": "Beim Absenden/Speichern. event.detail enthält die vollständigen Metadaten.",
    "uploadResult": "Nach dem Repository-Upload. event.detail = {success, nodeId, error, duplicate, repositoryUrl}.",
    "reloadFromPage": "Nutzer hat 'Seite neu laden' geklickt (Plugin-Modus). Ohne Payload.",
}

# Dispatched *into* the component, not emitted by it.
_WIDGET_INCOMING_EVENTS = {
    "plugin-extract": "Extraktion von außen anstoßen: "
    "canvas.dispatchEvent(new CustomEvent('plugin-extract', "
    "{detail: {text, url, inputMode, reset}})).",
}


@app.get(
    "/widget/info",
    summary="Widget-Einbindung (Web Component)",
    description="""Informationen zur Einbindung der Metadata Agent Webkomponente in eigene Anwendungen.

Gibt alle verfügbaren Varianten, die benötigten Script-URLs und Beispiel-Code zurück.
""",
    tags=["Widget"],
)
async def widget_info(request: Request):
    """Return embedding info for the web component widget."""
    base = str(request.base_url).rstrip("/")
    dist_base = f"{base}/widget/dist"
    examples_base = f"{base}/widget/examples"

    return {
        "name": "metadata-agent-canvas",
        "version": settings.app_version,
        "description": "Angular Web Component zur Anzeige und Bearbeitung von Metadaten. "
        "Kann als <metadata-agent-canvas> Tag in beliebige Webanwendungen eingebettet werden.",
        "dist_base_url": dist_base,
        "scripts": {
            "required": [
                f"{dist_base}/runtime.js",
                f"{dist_base}/polyfills.js",
                f"{dist_base}/main.js",
            ],
            "styles": [
                f"{dist_base}/styles.css",
            ],
            # Self-hosted: styles.css carries the @font-face rules and loads
            # the files from widget/dist/fonts/. Nothing else has to be linked.
            "fonts": [],
        },
        "variants": {
            "full": {
                "description": "Volle Webkomponente mit Eingabebereich, KI-Extraktion, Statusbar und allen Layouts. "
                "Für interaktive Metadaten-Erfassung und -Bearbeitung.",
                "example_url": f"{examples_base}/full.html",
                "snippet": (
                    '<link rel="stylesheet" href="' + dist_base + '/styles.css">\n'
                    '<script src="' + dist_base + '/runtime.js" defer></script>\n'
                    '<script src="' + dist_base + '/polyfills.js" defer></script>\n'
                    '<script src="' + dist_base + '/main.js" defer></script>\n\n'
                    "<metadata-agent-canvas\n"
                    '  api-url="' + base + '"\n'
                    '  layout="default"\n'
                    '  show-input-area="true"\n'
                    '  show-status-bar="true"\n'
                    '  show-floating-controls="true">\n'
                    "</metadata-agent-canvas>"
                ),
                "attributes": _WIDGET_ATTRIBUTES,
            },
            "detail": {
                "description": "Nur-Lese Detailansicht. Mehrspaltig, ohne Eingabe. "
                "Für Repository-Detailseiten und Metadaten-Vorschau.",
                "example_url": f"{examples_base}/detail.html",
                "snippet": (
                    '<link rel="stylesheet" href="' + dist_base + '/styles.css">\n'
                    '<script src="' + dist_base + '/runtime.js" defer></script>\n'
                    '<script src="' + dist_base + '/polyfills.js" defer></script>\n'
                    '<script src="' + dist_base + '/main.js" defer></script>\n\n'
                    "<metadata-agent-canvas\n"
                    '  api-url="' + base + '"\n'
                    '  layout="detail"\n'
                    '  columns="4"\n'
                    '  readonly="true">\n'
                    "</metadata-agent-canvas>\n\n"
                    "<script>\n"
                    "  // Es gibt kein node-id-Attribut. Daten werden per JS gesetzt:\n"
                    "  document.querySelector('metadata-agent-canvas')\n"
                    "    .metadataInput = { /* Output von /generate */ };\n"
                    "</script>"
                ),
            },
            "minimal": {
                "description": "Kompakte, randlose Variante ohne Statusbar. "
                "Für Einbettung in bestehende Formulare oder Sidebars.",
                "example_url": f"{examples_base}/minimal.html",
                "snippet": (
                    '<link rel="stylesheet" href="' + dist_base + '/styles.css">\n'
                    '<script src="' + dist_base + '/runtime.js" defer></script>\n'
                    '<script src="' + dist_base + '/polyfills.js" defer></script>\n'
                    '<script src="' + dist_base + '/main.js" defer></script>\n\n'
                    "<metadata-agent-canvas\n"
                    '  api-url="' + base + '"\n'
                    '  layout="clean"\n'
                    '  show-input-area="true"\n'
                    '  show-status-bar="false"\n'
                    '  show-floating-controls="false">\n'
                    "</metadata-agent-canvas>"
                ),
            },
        },
        "layouts": _WIDGET_LAYOUTS,
        "layout_aliases": _WIDGET_LAYOUT_ALIASES,
        "attributes": _WIDGET_ATTRIBUTES,
        "events": _WIDGET_EVENTS,
        "incoming_events": _WIDGET_INCOMING_EVENTS,
        "examples": {
            "full": f"{examples_base}/full.html",
            "detail": f"{examples_base}/detail.html",
            "minimal": f"{examples_base}/minimal.html",
            "boilerplate": f"{examples_base}/boilerplate.html",
            "json_import": f"{examples_base}/json-import.html",
            "prueftisch": f"{examples_base}/prueftisch.html",
            "prueftisch_gross": f"{examples_base}/prueftisch-gross.html",
            "clean": f"{examples_base}/clean.html",
            "default": f"{examples_base}/default.html",
            "review_demo": f"{examples_base}/review-demo.html",
            "canvas_parameter_demo": f"{examples_base}/canvas-parameter-demo.html",
            "floating_controls_demo": f"{examples_base}/floating-controls-demo.html",
            "dual_instance_test": f"{examples_base}/dual-instance-test.html",
            "uri_test": f"{examples_base}/uri-test.html",
            "interactive_test": f"{examples_base}/test.html",
        },
        "example_data": {
            "metadata_json": f"{examples_base}/metadata-2026-02-11.json",
        },
    }


# ============================================================================
# Health & Info Endpoints
# ============================================================================


@app.get("/health", include_in_schema=False)
async def health_check():
    """Liveness: the process answers (infra.py). Also on the internal port."""
    return health_payload(settings)


@app.get("/ready", include_in_schema=False)
async def readiness_check():
    """Readiness: configuration that can work at all (infra.py). 503 if not."""
    return readiness_response(settings)


@app.get("/debug/auth", include_in_schema=False)
async def debug_auth(request: Request):
    """
    Does a forwarded B-API key arrive? Masked - presence, scheme, length only.

    Exists only with METADATA_AGENT_DEBUG_ENDPOINTS=true; meant for setting up
    the B-API proxy (forwardApiKey), not for normal operation.
    """
    if not settings.debug_endpoints:
        raise HTTPException(status_code=404, detail="Not Found")
    return {
        "mode": settings.b_api_auth_mode,
        "Authorization": mask_header(request.headers.get("authorization")),
        "X-API-KEY": mask_header(request.headers.get("x-api-key")),
        "b_api_key_configured": bool(settings.b_api_key),
        "llm_calls_use": current_auth_source(settings),
    }


@app.get("/debug/dependencies", include_in_schema=False)
async def debug_dependencies():
    """
    Are B-API, text extraction and repository reachable - and is the key taken?

    Exists only with METADATA_AGENT_DEBUG_ENDPOINTS=true (services/diagnostics.py).
    """
    if not settings.debug_endpoints:
        raise HTTPException(status_code=404, detail="Not Found")
    return await check_dependencies(settings)


@app.get(
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
        default_context=settings.default_context,
    )


@app.get(
    "/info/schemas/{context}/{version}",
    response_model=list[SchemaInfo],
    summary="Schemas für Kontext/Version",
    description="""Listet alle verfügbaren Schemas (Content-Types) für einen Kontext und Version.

## Path-Parameter

- **context**: Schema-Kontext, z.B. `default`
- **version**: Schema-Version, z.B. `1.8.1` oder `latest`

## Response

Liste von Schemas mit:
- **file**: Schema-Dateiname (z.B. `event.json`)
- **profile_id**: Profil-ID für edu-sharing
- **label**: Mehrsprachiges Label
- **groups**: Anzahl Feldgruppen
- **field_count**: Anzahl Felder
""",
)
async def get_schemas_for_version(context: str, version: str):
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


@app.get(
    "/info/schema/{context}/{version}/{schema_file}",
    summary="Schema-Definition abrufen",
    description="""Gibt die vollständige Schema-Definition als JSON zurück.

## Path-Parameter

- **context**: Schema-Kontext, z.B. `default`
- **version**: Schema-Version, z.B. `1.8.1`
- **schema_file**: Schema-Datei, z.B. `event.json`, `core.json`

## Response

Vollständiges Schema mit:
- **fields**: Alle Felder mit ID, Label, Typ, Vokabular etc.
- **groups**: Feldgruppen für UI-Anzeige
- **metadata**: Schema-Metadaten
""",
)
async def get_schema_definition(context: str, version: str, schema_file: str):
    """Get full schema definition."""
    try:
        schema = load_schema(context, version, schema_file)
        return schema
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================================
# Content Type Detection
# ============================================================================


@app.post(
    "/detect-content-type",
    response_model=DetectContentTypeResponse,
    summary="Content-Type erkennen",
    description="""Analysiert Text und erkennt automatisch den passenden Content-Type (Schema) für die Metadaten-Extraktion.

## Input-Quellen (input_source)

| Wert | Beschreibung | Benötigte Felder |
|------|-------------|------------------|
| `text` | Direkter Text als Eingabe (Standard) | `text` |
| `url` | Text von URL via Crawler abrufen | `source_url`, optional `extraction_method` |
| `node_id` | Text + Metadaten von Repository-Node abrufen | `node_id`, `repository` |
| `node_url` | Repository-Node + Crawler-Fallback (URL aus ccm:wwwurl) | `node_id`, `repository` (source_url optional) |

## Extraction Method (extraction_method)

| Wert | Beschreibung |
|------|-------------|
| `simple` | Schnelle HTML-Extraktion |
| `browser` | Vollständiges Browser-Rendering für JS-Seiten (Standard) |

## Output Format (output_format)

| Wert | Beschreibung |
|------|-------------|
| `markdown` | Markdown-Format (Standard) |
| `txt` | Reiner Text |
| `html` | Rohes HTML |

## Repository

> **Deprecated:** Der Parameter `repository` wird ignoriert. Die Repository-URL wird über `METADATA_AGENT_REPOSITORY_URL` konfiguriert. Der Parameter wird aus Abwärtskompatibilität akzeptiert.

## LLM-Optionen

- **llm_provider**: `b-api-openai` (Standard), `b-api-academiccloud`, `b-api-router`, `openai`
- **llm_model**: Ohne Angabe gilt der Default des Providers — `gpt-6-luna` (b-api-openai), `deepseek-v4-flash` (b-api-academiccloud), `openai/gpt-6-luna` (b-api-router, Routenname oder provider/modell), `gpt-4o-mini` (openai)
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/DetectContentTypeRequest"},
                    "examples": {
                        "text_input": {
                            "summary": "1. Text-Eingabe",
                            "description": "Direkter Text als Eingabe.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.\nLernen Sie die Grundlagen der künstlichen Intelligenz kennen.",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "context": "default",
                                "version": "latest",
                                "language": "de",
                            },
                        },
                        "url_input": {
                            "summary": "2. URL-Eingabe",
                            "description": "Text von URL abrufen.",
                            "value": {
                                "input_source": "url",
                                "text": "",
                                "source_url": "https://www.wirlernenonline.de",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "context": "default",
                                "version": "latest",
                                "language": "de",
                            },
                        },
                        "node_id_input": {
                            "summary": "3. NodeID-Eingabe",
                            "description": "Text von Repository-Node abrufen.",
                            "value": {
                                "input_source": "node_id",
                                "text": "",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
                                "context": "default",
                                "version": "latest",
                                "language": "de",
                            },
                        },
                    },
                }
            }
        }
    },
)
async def detect_content_type(req: DetectContentTypeRequest):
    """
    Detect content type from text using LLM.

    Returns the detected content type along with all available content types
    for the specified context and version.
    """
    start_time = time.time()

    # Handle input source
    text = req.text
    # How the text was obtained, where the client should know (node_url fallback)
    input_warnings: list[str] = []
    if req.input_source != InputSource.TEXT:
        input_service = get_input_source_service()
        try:
            if req.input_source == InputSource.URL:
                if not req.source_url:
                    raise HTTPException(
                        status_code=400,
                        detail="source_url required for input_source='url'",
                    )
                extracted_text = await input_service.fetch_from_url(
                    req.source_url,
                    req.extraction_method.value,
                    lang=req.language,
                    output_format=req.output_format.value,
                )
                # Prepend source URL so LLM can use URL path signals for classification
                text = f"Quell-URL / Source URL: {req.source_url}\n\n{extracted_text}"
            elif req.input_source == InputSource.NODE_ID:
                if not req.node_id:
                    raise HTTPException(
                        status_code=400,
                        detail="node_id required for input_source='node_id'",
                    )
                input_data = await input_service.fetch_from_node_id(req.node_id)
                if input_data.source_url:
                    text = f"Quell-URL / Source URL: {input_data.source_url}\n\n{input_data.text}"
                else:
                    text = input_data.text
            elif req.input_source == InputSource.NODE_URL:
                if not req.node_id:
                    raise HTTPException(
                        status_code=400,
                        detail="node_id required for input_source='node_url'",
                    )
                input_data = await input_service.fetch_from_node_url(
                    req.node_id,
                    source_url=req.source_url or None,
                    extraction_method=req.extraction_method.value,
                    lang=req.language,
                    output_format=req.output_format.value,
                )
                source_url_info = input_data.source_url or req.source_url
                if source_url_info:
                    text = f"Quell-URL / Source URL: {source_url_info}\n\n{input_data.text}"
                else:
                    text = input_data.text
                input_warnings = input_data.warnings
        except HTTPException:
            raise
        except TextExtractionError as e:
            return text_extraction_error_response(e)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to fetch input: {str(e) or type(e).__name__}",
            )

    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    # Truncate excessively long text to prevent exceeding LLM context window
    MAX_TEXT_LENGTH = 500_000
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH]

    # Resolve "latest" to actual version
    version = req.version
    if version == "latest" or not version:
        version = get_latest_version(req.context)

    # Get available content types
    content_types = get_content_types(req.context, version)

    if not content_types:
        raise HTTPException(
            status_code=404,
            detail=f"No content types defined for context '{req.context}' version '{version}'",
        )

    # Build available list
    available = []
    for ct in content_types:
        schema_file = ct.get("schema_file", "")
        available.append(
            ContentTypeInfo(
                schema_file=schema_file,
                uri=ct.get("uri") or None,
                profile_id=ct.get("profile_id"),
                label=LocalizedString(
                    de=ct.get("label", {}).get("de", schema_file),
                    en=ct.get("label", {}).get("en", schema_file),
                ),
                confidence=None,
            )
        )

    # Use LLM to detect content type (with optional overrides). A fallback is
    # labelled as one; a refused key is an error, never "learning material".
    check_provider_override(req.llm_provider)
    service = get_metadata_service(
        llm_provider=req.llm_provider, llm_model=req.llm_model
    )
    try:
        detection = await service.detect_schema(
            text, req.context, version, req.language
        )
    except LLMError as e:
        return llm_error_response(e)
    finally:
        # Close non-default LLM service HTTP client to prevent leak
        if req.llm_provider is not None or req.llm_model is not None:
            await service.llm_service.close()
    detected_schema = detection["schema_file"]

    # Find the detected content type info
    detected_info = None
    for ct in content_types:
        if ct.get("schema_file") == detected_schema:
            detected_info = ContentTypeInfo(
                schema_file=detected_schema,
                uri=ct.get("uri") or None,
                profile_id=ct.get("profile_id"),
                label=LocalizedString(
                    de=ct.get("label", {}).get("de", detected_schema),
                    en=ct.get("label", {}).get("en", detected_schema),
                ),
                confidence=detection["confidence"],
            )
            break

    if not detected_info:
        # Fallback if detection returned unknown schema
        detected_info = ContentTypeInfo(
            schema_file=detected_schema,
            uri=None,
            profile_id=None,
            label=LocalizedString(de=detected_schema, en=detected_schema),
            confidence="low",
        )

    processing_time = int((time.time() - start_time) * 1000)

    return DetectContentTypeResponse(
        detected=detected_info,
        available=available,
        context=req.context,
        version=version,
        processing_time_ms=processing_time,
        method=detection["method"],
        warnings=input_warnings
        + ([detection["warning"]] if detection.get("warning") else []),
    )


# ============================================================================
# Single Field Extraction
# ============================================================================


@app.post(
    "/extract-field",
    response_model=ExtractFieldResponse,
    summary="Einzelnes Feld extrahieren",
    description="""Extrahiert oder regeneriert einen einzelnen Feldwert mittels LLM. Nützlich um einzelne Felder zu korrigieren ohne alles neu zu extrahieren.

## Input-Quellen (input_source)

| Wert | Beschreibung | Benötigte Felder |
|------|-------------|------------------|
| `text` | Direkter Text als Eingabe (Standard) | `text` |
| `url` | Text von URL via Crawler abrufen | `source_url`, optional `extraction_method` |
| `node_id` | Text + Metadaten von Repository-Node abrufen | `node_id`, `repository` |
| `node_url` | Repository-Node + Crawler-Fallback (URL aus ccm:wwwurl) | `node_id`, `repository` (source_url optional) |

## Extraction Method (extraction_method)

| Wert | Beschreibung |
|------|-------------|
| `simple` | Schnelle HTML-Extraktion |
| `browser` | Vollständiges Browser-Rendering für JS-Seiten (Standard) |

## Output Format (output_format)

| Wert | Beschreibung |
|------|-------------|
| `markdown` | Markdown-Format (Standard) |
| `txt` | Reiner Text |
| `html` | Rohes HTML |

## Repository

> **Deprecated:** Der Parameter `repository` wird ignoriert. Die Repository-URL wird über `METADATA_AGENT_REPOSITORY_URL` konfiguriert.

## Feld-Optionen

- **schema_file**: Schema-Datei die das Feld enthält, z.B. `event.json`, `core.json`
- **field_id**: Feld-ID zum Extrahieren, z.B. `ccm:oeh_event_begin`, `cclom:title`
- **existing_metadata**: Bestehende Metadaten als Kontext (optional)
- **normalize**: Normalisierung anwenden (Standard: `true`)

## LLM-Optionen

- **llm_provider**: `b-api-openai` (Standard), `b-api-academiccloud`, `b-api-router`, `openai`
- **llm_model**: Ohne Angabe gilt der Default des Providers — `gpt-6-luna` (b-api-openai), `deepseek-v4-flash` (b-api-academiccloud), `openai/gpt-6-luna` (b-api-router, Routenname oder provider/modell), `gpt-4o-mini` (openai)
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ExtractFieldRequest"},
                    "examples": {
                        "text_input": {
                            "summary": "1. Text-Eingabe",
                            "description": "Extrahiert ein Feld aus direktem Text.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "field_id": "ccm:oeh_event_begin",
                                "existing_metadata": {},
                                "language": "de",
                                "normalize": True,
                            },
                        },
                        "url_input": {
                            "summary": "2. URL-Eingabe",
                            "description": "Extrahiert ein Feld aus Text einer URL.",
                            "value": {
                                "input_source": "url",
                                "text": "",
                                "source_url": "https://www.wirlernenonline.de",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "field_id": "cclom:title",
                                "existing_metadata": {},
                                "language": "de",
                                "normalize": True,
                            },
                        },
                        "node_id_input": {
                            "summary": "3. NodeID-Eingabe",
                            "description": "Extrahiert ein Feld aus Repository-Node Daten.",
                            "value": {
                                "input_source": "node_id",
                                "text": "",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "field_id": "ccm:oeh_event_begin",
                                "existing_metadata": {},
                                "language": "de",
                                "normalize": True,
                            },
                        },
                        "feld_korrigieren": {
                            "summary": "4. Feld korrigieren",
                            "description": "Korrigiert ein Feld basierend auf existing_metadata.",
                            "value": {
                                "input_source": "text",
                                "text": "Der Workshop wurde auf den 20. März 2025 verschoben.",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "field_id": "ccm:oeh_event_begin",
                                "existing_metadata": {
                                    "ccm:oeh_event_begin": "2025-03-15T00:00"
                                },
                                "language": "de",
                                "normalize": True,
                            },
                        },
                    },
                }
            }
        }
    },
)
async def extract_field(req: ExtractFieldRequest):
    """
    Extract a single field from text.

    Use this endpoint to:
    - Fix individual fields without full re-extraction
    - Test different LLM models on specific fields
    - Update a single field with new information

    The extraction uses the same LLM pipeline as /generate but for one field only.
    Normalization is applied by default (dates, vocabularies, etc.).
    """
    start_time = time.time()

    # Handle input source
    text = req.text
    existing_metadata = req.existing_metadata or {}
    # How the text was obtained, where the client should know (node_url fallback)
    input_warnings: list[str] = []

    if req.input_source != InputSource.TEXT:
        input_service = get_input_source_service()
        try:
            if req.input_source == InputSource.URL:
                if not req.source_url:
                    raise HTTPException(
                        status_code=400,
                        detail="source_url required for input_source='url'",
                    )
                extracted_text = await input_service.fetch_from_url(
                    req.source_url,
                    req.extraction_method.value,
                    lang=req.language,
                    output_format=req.output_format.value,
                )
                text = f"Quell-URL / Source URL: {req.source_url}\n\n{extracted_text}"
            elif req.input_source == InputSource.NODE_ID:
                if not req.node_id:
                    raise HTTPException(
                        status_code=400,
                        detail="node_id required for input_source='node_id'",
                    )
                input_data = await input_service.fetch_from_node_id(req.node_id)
                # Prepend source URL if available (from ccm:wwwurl) so LLM can use it
                if input_data.source_url:
                    text = f"Quell-URL / Source URL: {input_data.source_url}\n\n{input_data.text}"
                else:
                    text = input_data.text
                if input_data.existing_metadata:
                    existing_metadata = {
                        **input_data.existing_metadata,
                        **existing_metadata,
                    }
            elif req.input_source == InputSource.NODE_URL:
                if not req.node_id:
                    raise HTTPException(
                        status_code=400,
                        detail="node_id required for input_source='node_url'",
                    )
                input_data = await input_service.fetch_from_node_url(
                    req.node_id,
                    source_url=req.source_url or None,
                    extraction_method=req.extraction_method.value,
                    lang=req.language,
                    output_format=req.output_format.value,
                )
                source_url_info = input_data.source_url or req.source_url
                if source_url_info:
                    text = f"Quell-URL / Source URL: {source_url_info}\n\n{input_data.text}"
                else:
                    text = input_data.text
                if input_data.existing_metadata:
                    existing_metadata = {
                        **input_data.existing_metadata,
                        **existing_metadata,
                    }
                input_warnings = input_data.warnings
        except HTTPException:
            raise
        except TextExtractionError as e:
            return text_extraction_error_response(e)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to fetch input: {str(e) or type(e).__name__}",
            )

    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    # Truncate excessively long text to prevent exceeding LLM context window
    MAX_TEXT_LENGTH = 500_000
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH]

    # Resolve "latest" to actual version
    version = req.version
    if version == "latest" or not version:
        version = get_latest_version(req.context)

    # Resolve schema_file: accept URI or filename
    resolved_schema_file = req.schema_file
    try:
        resolved_schema_file = resolve_schema_file_or_uri(
            req.schema_file, req.context, version
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Load schema to get field definition
    try:
        schema = load_schema(req.context, version, resolved_schema_file)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Find the field in the schema
    field_def = None
    for field in schema.get("fields", []):
        if field.get("id") == req.field_id:
            field_def = field
            break

    if not field_def:
        # Also check core.json if not found
        try:
            core_schema = load_schema(req.context, version, "core.json")
            for field in core_schema.get("fields", []):
                if field.get("id") == req.field_id:
                    field_def = field
                    break
        except Exception:
            pass

    if not field_def:
        raise HTTPException(
            status_code=404,
            detail=f"Field '{req.field_id}' not found in schema '{resolved_schema_file}' or core.json",
        )

    # Get LLM service with optional overrides
    check_provider_override(req.llm_provider)
    llm_service = get_llm_service(
        llm_provider=req.llm_provider, llm_model=req.llm_model
    )

    # Get existing value from existing_metadata if provided
    existing_value = None
    if existing_metadata:
        existing_value = existing_metadata.get(req.field_id)

    # Extract the field (returns tuple: field_id, value, error)
    try:
        _, raw_value, error = await llm_service.extract_field(
            field=field_def,
            text=text,
            existing_value=existing_value,
            language=req.language,
        )
    except LLMError as e:
        return llm_error_response(e)
    finally:
        # Close non-default LLM service HTTP client to prevent leak
        if req.llm_provider is not None or req.llm_model is not None:
            await llm_service.close()

    if isinstance(error, LLMError):
        return llm_error_response(error)
    if error:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {error}")

    # Apply normalization if requested
    normalized = False
    value = raw_value

    if req.normalize and raw_value is not None:
        normalizer = get_field_normalizer()
        normalized_value = normalizer.normalize_field_value(
            value=raw_value,
            field_schema=field_def.get("system", field_def),
            normalize_vocabularies=True,
        )
        if normalized_value != raw_value:
            value = normalized_value
            normalized = True

    # Determine if value changed
    changed = value != existing_value

    # Get field label
    field_label = field_def.get("label", {})
    if isinstance(field_label, dict):
        field_label = field_label.get(req.language, field_label.get("de", req.field_id))

    processing_time = int((time.time() - start_time) * 1000)

    return ExtractFieldResponse(
        field_id=req.field_id,
        field_label=field_label,
        value=value,
        raw_value=raw_value if normalized else None,
        previous_value=existing_value,
        changed=changed,
        normalized=normalized,
        context=req.context,
        version=version,
        schema_file=resolved_schema_file,
        processing={
            "llm_provider": llm_service.provider,
            "llm_model": llm_service.model,
            "processing_time_ms": processing_time,
            "warnings": input_warnings,
        },
    )


# ============================================================================
# Main Endpoints
# ============================================================================


@app.post(
    "/generate",
    response_model=GenerateResponse,
    summary="Metadaten generieren",
    description="""Generiert vollständige Metadaten aus Text, URL oder Repository-Node mittels LLM.

## Input-Quellen (input_source)

| Wert | Beschreibung | Benötigte Felder |
|------|-------------|------------------|
| `text` | Direkter Text als Eingabe (Standard) | `text` |
| `url` | Text von URL via Crawler abrufen | `source_url`, optional `extraction_method` |
| `node_id` | Metadaten + hinterlegte Volltexte von Repository-Node | `node_id`, `repository` |
| `node_url` | Repository-Node + Crawler-Fallback (URL aus ccm:wwwurl) | `node_id`, `repository` (source_url optional, wird aus Metadaten geholt) |

## Extraction Method (extraction_method)

| Wert | Beschreibung |
|------|-------------|
| `simple` | Schnelle HTML-Extraktion |
| `browser` | Vollständiges Browser-Rendering für JS-Seiten (Standard) |

## Output Format (output_format)

| Wert | Beschreibung |
|------|-------------|
| `markdown` | Markdown-Format (Standard) |
| `txt` | Reiner Text |
| `html` | Rohes HTML |

## Repository

> **Deprecated:** Der Parameter `repository` wird ignoriert. Die Repository-URL wird über `METADATA_AGENT_REPOSITORY_URL` konfiguriert.

## Schema-Optionen

- **context**: Schema-Kontext, z.B. `default`, `mds_oeh`
- **version**: Schema-Version, `latest` (Standard) oder spezifisch z.B. `1.8.1`
- **schema_file**: `auto` (automatische Erkennung), oder spezifisch z.B. `event.json`

## Extraktions-Optionen

- **language**: Sprache für Extraktion (`de` oder `en`)
- **max_workers**: Parallele LLM-Worker (1-20). Ohne Angabe gilt `METADATA_AGENT_DEFAULT_MAX_WORKERS` (Standard 20); zusätzlich durch das Durchsatzlimit des Providers gedeckelt
- **include_core**: Core-Felder einbeziehen (Standard: `true`)
- **enable_geocoding**: Adressen zu Koordinaten umwandeln (Standard: `true`)
- **normalize**: Normalisierung für Datum, Boolean, Vokabulare (Standard: `true`)

## Regenerations-Optionen

- **existing_metadata**: Bestehende Metadaten als Basis (werden erweitert)
- **regenerate_fields**: Liste von Feld-IDs die neu extrahiert werden sollen
- **regenerate_empty**: Leere Felder in existing_metadata neu extrahieren (Standard: `false`)

## Screenshot-Optionen (Vorschaubild parallel zur Extraktion)

- **screenshot_method**: `playwright` (lokal) oder der Legacy-Alias `pageshot` (nimmt ebenfalls lokal auf, kein externer Dienst). Wenn nicht gesetzt: kein Screenshot.
- **preview_url**: URL für Screenshot (optional). Wenn nicht gesetzt: wird aus `source_url` oder bestehenden Metadaten (`ccm:wwwurl`) ermittelt.
- Antwort enthält `preview_image_url` als Base64 Data-URL wenn Screenshot erfolgreich.

## LLM-Optionen

- **llm_provider**: `b-api-openai` (Standard), `b-api-academiccloud`, `b-api-router`, `openai`
- **llm_model**: Ohne Angabe gilt der Default des Providers — `gpt-6-luna` (b-api-openai), `deepseek-v4-flash` (b-api-academiccloud), `openai/gpt-6-luna` (b-api-router, Routenname oder provider/modell), `gpt-4o-mini` (openai)
""",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/GenerateRequest"},
                    "examples": {
                        "text_input": {
                            "summary": "1. Text-Eingabe (Standard)",
                            "description": "Direkter Text als Eingabe. Schema wird automatisch erkannt.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.\nLernen Sie die Grundlagen der künstlichen Intelligenz kennen.",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "existing_metadata": {},
                                "context": "default",
                                "version": "latest",
                                "schema_file": "auto",
                                "language": "de",
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": [],
                                "regenerate_empty": False,
                                "screenshot_method": "",
                                "preview_url": "",
                            },
                        },
                        "url_input": {
                            "summary": "2. URL-Eingabe (Crawler)",
                            "description": "Text von URL abrufen via Text-Extraction-API. extraction_method: 'simple' (schnell) oder 'browser' (JS-Rendering). Mit screenshot_method wird parallel ein Vorschaubild erstellt.",
                            "value": {
                                "input_source": "url",
                                "text": "",
                                "source_url": "https://www.wirlernenonline.de",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "existing_metadata": {},
                                "context": "default",
                                "version": "latest",
                                "schema_file": "auto",
                                "language": "de",
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": [],
                                "regenerate_empty": False,
                                "screenshot_method": "playwright",
                                "preview_url": "",
                            },
                        },
                        "node_id_input": {
                            "summary": "3. NodeID-Eingabe (Repository)",
                            "description": "Metadaten + hinterlegte Volltexte von Repository-Node abrufen.",
                            "value": {
                                "input_source": "node_id",
                                "text": "",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
                                "existing_metadata": {},
                                "context": "default",
                                "version": "latest",
                                "schema_file": "auto",
                                "language": "de",
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": [],
                                "regenerate_empty": False,
                                "screenshot_method": "playwright",
                                "preview_url": "",
                            },
                        },
                        "node_url_input": {
                            "summary": "4. NodeID+URL (kombiniert)",
                            "description": "Repository-Metadaten + Volltext nutzen. Falls kein Volltext: URL aus ccm:wwwurl holen und Crawler nutzen.",
                            "value": {
                                "input_source": "node_url",
                                "text": "",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "cbf66543-fb90-4e69-a392-03f305139e3f",
                                "existing_metadata": {},
                                "context": "default",
                                "version": "latest",
                                "schema_file": "auto",
                                "language": "de",
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": [],
                                "regenerate_empty": False,
                                "screenshot_method": "playwright",
                                "preview_url": "",
                            },
                        },
                        "mit_existing_metadata": {
                            "summary": "5. Mit bestehenden Metadaten",
                            "description": "existing_metadata als Basis, nur bestimmte Felder neu extrahieren.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                                "source_url": "",
                                "extraction_method": "browser",
                                "output_format": "markdown",
                                "node_id": "",
                                "existing_metadata": {
                                    "cclom:title": "Mein Workshop",
                                    "cclom:general_keyword": ["KI", "Bildung"],
                                },
                                "context": "default",
                                "version": "latest",
                                "schema_file": "event.json",
                                "language": "de",
                                "include_core": True,
                                "enable_geocoding": True,
                                "normalize": True,
                                "regenerate_fields": ["ccm:oeh_event_begin"],
                                "regenerate_empty": True,
                                "screenshot_method": "",
                                "preview_url": "",
                            },
                        },
                        "anderer_provider": {
                            "summary": "6. Anderer LLM-Provider",
                            "description": "Provider und Modell nur für diese Anfrage. AcademicCloud verträgt 2 Requests gleichzeitig — mehr Worker warten nur.",
                            "value": {
                                "input_source": "text",
                                "text": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.",
                                "llm_provider": "b-api-academiccloud",
                                "llm_model": "deepseek-v4-flash",
                            },
                        },
                    },
                },
                "text/plain": {
                    "schema": {"type": "string"},
                    "examples": {
                        "mehrzeiliger_text": {
                            "summary": "Mehrzeiliger Text (ohne JSON)",
                            "description": "Für mehrzeilige Texte: Einfach den Text einfügen. Schema wird automatisch erkannt.",
                            "value": "Workshop 'KI in der Bildung' am 15. März 2025 in Berlin.\n\nLernen Sie die Grundlagen der künstlichen Intelligenz kennen.\n\nZielgruppe: Lehrkräfte\nKosten: 49 Euro",
                        }
                    },
                },
            }
        }
    },
)
async def generate_metadata(request: Request):
    """
    Generate metadata from text.

    Extracts metadata from text using AI. Control characters are auto-sanitized.

    **Output Format:**
    - Header: contextName, schemaVersion, metadataset, language, exportedAt
    - metadata: Flat key-value pairs (field_id: value)
    - processing: Success status, stats, LLM info, errors/warnings
    """
    # Check content type to handle both JSON and plain text
    content_type = request.headers.get("content-type", "application/json")

    try:
        raw_body = await request.body()
        body_str = raw_body.decode("utf-8")

        if "text/plain" in content_type:
            # Plain text mode: text is the body, use defaults for other params
            data = {
                "text": body_str,
                "context": "default",
                "version": "latest",
                "schema_file": "auto",
                "language": "de",
                "include_core": True,
            }
        else:
            # JSON mode: parse and sanitize
            sanitized = sanitize_json_string(body_str)

            try:
                data = json.loads(sanitized)
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid JSON: {str(e)}. Tip: Use Content-Type 'text/plain' for multi-line text input.",
                )

        # Without max_workers the ENV default applies (Settings keeps it
        # inside the range the request model accepts).
        if isinstance(data, dict) and "max_workers" not in data:
            data["max_workers"] = settings.default_max_workers

        # Validate with Pydantic
        try:
            req = GenerateRequest(**data)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Request parsing error: {str(e)}")

    # Handle different input sources
    text = req.text
    existing_metadata = req.existing_metadata or {}
    # How the text was obtained, where the client should know (node_url fallback)
    input_warnings: list[str] = []

    # Extract _origins and _source_text from existing_metadata (passed by web component)
    origins = existing_metadata.pop("_origins", None) if existing_metadata else None
    original_source_text = (
        existing_metadata.pop("_source_text", None) if existing_metadata else None
    )

    # Re-extraction: combine original source text + extracted values + correction instruction.
    # The LLM needs three things to handle corrections properly:
    # 1. The original source document (context)
    # 2. The extracted values from pass 1 (BISHERIGER STAND — what was extracted)
    # 3. The correction instruction (what to change)
    if original_source_text and text and text.strip():
        # Build a human-readable summary of the current extracted metadata
        metadata_lines = []
        for key, value in existing_metadata.items():
            if not key.startswith("_"):
                if isinstance(value, list):
                    metadata_lines.append(
                        f"  {key}: {', '.join(str(v) for v in value)}"
                    )
                else:
                    metadata_lines.append(f"  {key}: {value}")

        bisheriger_stand = "\n".join(metadata_lines) if metadata_lines else "(keine)"

        combined = (
            f"{original_source_text}\n\n"
            f"--- BISHERIGER STAND (extrahierte Metadaten aus Durchlauf 1) ---\n"
            f"{bisheriger_stand}\n\n"
            f"--- KORREKTUR / UPDATE ---\n"
            f"{text.strip()}"
        )
        logger.info(
            f"🔄 RE-EXTRACTION: source ({len(original_source_text)} chars) + {len(metadata_lines)} extracted fields + instruction → {len(combined)} chars total"
        )
        text = combined

    if req.input_source == InputSource.TEXT:
        # Direct text input
        if not text or not text.strip():
            raise HTTPException(
                status_code=400, detail="Text is required when input_source='text'"
            )

    elif req.input_source == InputSource.URL:
        # Fetch text from URL via text extraction API
        if not req.source_url:
            raise HTTPException(
                status_code=400, detail="source_url is required when input_source='url'"
            )
        try:
            input_service = get_input_source_service()
            extracted_text = await input_service.fetch_from_url(
                url=req.source_url,
                method=req.extraction_method.value,
                lang=req.language,
                output_format=req.output_format.value,
            )
            # Prepend source URL to text so LLM can use it for ccm:wwwurl field
            text = f"Quell-URL / Source URL: {req.source_url}\n\n{extracted_text}"
        except PublicUrlError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except TextExtractionError as e:
            return text_extraction_error_response(e)
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch text from URL: {str(e) or type(e).__name__}",
            )

    elif req.input_source == InputSource.NODE_ID:
        # Fetch from repository by NodeID
        if not req.node_id:
            raise HTTPException(
                status_code=400,
                detail="node_id is required when input_source='node_id'",
            )
        try:
            input_service = get_input_source_service()
            input_data = await input_service.fetch_from_node_id(node_id=req.node_id)
            # Prepend source URL if available (from ccm:wwwurl) so LLM can use it
            if input_data.source_url:
                text = f"Quell-URL / Source URL: {input_data.source_url}\n\n{input_data.text}"
            else:
                text = input_data.text
            # Merge fetched metadata with provided existing_metadata (provided takes precedence)
            existing_metadata = (
                {**input_data.existing_metadata, **existing_metadata}
                if input_data.existing_metadata
                else existing_metadata
            )
        except Exception as e:
            raise HTTPException(
                status_code=502, detail=f"Failed to fetch from repository: {str(e)}"
            )

    elif req.input_source == InputSource.NODE_URL:
        # Fetch from repository + URL fallback (URL from ccm:wwwurl if not provided)
        if not req.node_id:
            raise HTTPException(
                status_code=400,
                detail="node_id is required when input_source='node_url'",
            )
        try:
            input_service = get_input_source_service()
            input_data = await input_service.fetch_from_node_url(
                node_id=req.node_id,
                source_url=req.source_url or None,
                extraction_method=req.extraction_method.value,
                lang=req.language,
                output_format=req.output_format.value,
            )
            input_warnings = input_data.warnings
            # Prepend source URL to text so LLM can use it for ccm:wwwurl field
            source_url_info = input_data.source_url or req.source_url
            if source_url_info:
                text = f"Quell-URL / Source URL: {source_url_info}\n\n{input_data.text}"
            else:
                text = input_data.text
            # Merge fetched metadata with provided existing_metadata (provided takes precedence)
            existing_metadata = (
                {**input_data.existing_metadata, **existing_metadata}
                if input_data.existing_metadata
                else existing_metadata
            )
        except Exception as e:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch input data: {str(e) or type(e).__name__}",
            )

    if not text or not text.strip():
        raise HTTPException(
            status_code=400, detail="No text content available from input source"
        )

    # Truncate excessively long text to prevent exceeding LLM context window
    MAX_TEXT_LENGTH = 500_000  # ~125k tokens — generous limit for long web pages
    if len(text) > MAX_TEXT_LENGTH:
        original_len = len(text)
        text = text[:MAX_TEXT_LENGTH]
        logger.warning(
            f"⚠️ Text truncated from {original_len} to {MAX_TEXT_LENGTH} characters"
        )

    # Resolve "latest" to actual version
    version = req.version
    if version == "latest" or not version:
        version = get_latest_version(req.context)

    # Resolve schema_file: accept URI or filename
    schema_file = req.schema_file
    if schema_file and schema_file != "auto":
        try:
            schema_file = resolve_schema_file_or_uri(schema_file, req.context, version)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Get service with optional LLM overrides
    check_provider_override(req.llm_provider)
    service = get_metadata_service(
        llm_provider=req.llm_provider, llm_model=req.llm_model
    )
    # An override gets its own LLMService with its own HTTP client, which this
    # request has to close on every way out.
    own_llm_client = req.llm_provider is not None or req.llm_model is not None

    # --- Async screenshot capture (parallel to KI extraction) ---
    screenshot_task = None
    if req.screenshot_method:
        import asyncio
        import base64

        # Determine screenshot URL: explicit preview_url > source_url > from existing metadata
        screenshot_url = req.preview_url or req.source_url
        if not screenshot_url and existing_metadata:
            screenshot_url = existing_metadata.get("ccm:wwwurl")
            if isinstance(screenshot_url, list):
                screenshot_url = screenshot_url[0] if screenshot_url else None

        if screenshot_url:
            method = (
                req.screenshot_method
                if req.screenshot_method in LOCAL_METHODS
                else "playwright"
            )
            try:
                ss_service = get_screenshot_service()
                logger.info(
                    f"📸 Starting async screenshot in /generate: {screenshot_url} (method: {method})"
                )
                screenshot_task = asyncio.create_task(
                    ss_service.capture(url=screenshot_url, method=method)
                )
            except Exception as e:
                logger.warning(f"⚠️ Screenshot task creation failed: {e}")

    try:
        result = await service.generate_metadata(
            text=text,
            context=req.context,
            version=version,
            schema_file=schema_file,
            existing_metadata=existing_metadata,
            language=req.language,
            max_workers=req.max_workers,
            include_core=req.include_core,
            enable_geocoding=req.enable_geocoding,
            normalize_output=req.normalize,
            normalize_vocabularies=req.normalize,
            regenerate_fields=req.regenerate_fields,
            regenerate_empty=req.regenerate_empty,
            origins=origins,
        )
    except BaseException as error:
        # Nobody awaits the capture any more, and it holds one of the few
        # browser slots for up to MAX_CAPTURE_SECONDS.
        if screenshot_task:
            screenshot_task.cancel()
        # A cancelled request (client gone) is not a failed generation.
        if isinstance(error, Exception):
            GENERATIONS.labels("failed").inc()
        if own_llm_client:
            await service.llm_service.close()
        raise

    if input_warnings:
        processing = result.setdefault("processing", {})
        processing["warnings"] = input_warnings + processing.get("warnings", [])

    GENERATIONS.labels((result.get("processing") or {}).get("status", "unknown")).inc()

    if (result.get("processing") or {}).get("status") == "failed":
        # Not one usable field: answer with the error instead of an empty
        # "success" that a client would apply over existing metadata.
        if screenshot_task:
            screenshot_task.cancel()
        if own_llm_client:
            await service.llm_service.close()
        return generation_failed_response(result)

    # --- Await screenshot result ---
    preview_image_url = None
    if screenshot_task:
        try:
            ss_result = await screenshot_task
            if ss_result and ss_result.image_bytes:
                # Return as base64 data URL for immediate display in web component
                b64 = base64.b64encode(ss_result.image_bytes).decode("ascii")
                preview_image_url = (
                    f"data:image/{ss_result.format or 'png'};base64,{b64}"
                )
                logger.info(
                    f"📸 Screenshot captured: {len(ss_result.image_bytes)} bytes ({ss_result.capture_time_ms}ms)"
                )
        except Exception as e:
            logger.warning(f"⚠️ Screenshot capture failed: {e}")

    # Close non-default LLM service HTTP client to prevent leak
    if own_llm_client:
        await service.llm_service.close()

    # Resolve metadataset_uri from the detected/used schema_file
    metadataset_uri = get_content_type_uri(result["metadataset"], req.context, version)

    # Build flat response (metadata fields at top level)
    response = {
        "contextName": result["contextName"],
        "schemaVersion": result["schemaVersion"],
        "metadataset": result["metadataset"],
        "metadataset_uri": metadataset_uri,
        "language": result["language"],
        "exportedAt": result["exportedAt"],
    }

    # Add metadata fields directly (flat) - skip empty default values
    for key, value in result.get("metadata", {}).items():
        if value is not None and value != "" and value != [] and value != {}:
            response[key] = value

    # Add _origins tracking (ai/user per field)
    if result.get("_origins"):
        response["_origins"] = result["_origins"]

    # Add _source_text (raw text before extraction) for extended data upload
    if text:
        response["_source_text"] = text

    # Add preview image URL if screenshot was captured
    if preview_image_url:
        response["preview_image_url"] = preview_image_url

    # Add processing info at the end
    response["processing"] = result["processing"]

    return JSONResponse(content=response)


@app.post(
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
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Validation error: {str(e)}")

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

    service = get_metadata_service()
    result = service.validate_metadata(
        metadata=metadata,
        context=context,
        version=version,
        schema_file=schema_file,
    )

    return ValidateResponse(**result)


@app.post(
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
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Validation error: {str(e)}")

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

    service = get_metadata_service()
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


# ============================================================================
# Repository Upload Endpoint
# ============================================================================


@app.post(
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
6. **Workflow** (optional): Durchläuft `workflow_steps` Schritt für Schritt
7. **Node zurücklesen** (optional, `return_full_node`): Liest den fertigen Node erneut aus

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

    **Workflow:**
    1. Check for duplicates by ccm:wwwurl (optional)
    2. Create node with minimal data
    3. Set full metadata
    4. Add to collections (if specified)
    5. Start review workflow (optional)
    """
    # Parse JSON body
    data = parse_json_object(await request.body(), '{"cclom:title": "…"}')

    import asyncio

    # If direct metadata (no "metadata" wrapper), wrap it for Pydantic model.
    #
    # The options have to be lifted out by name before the rest becomes the
    # metadata dict. A name missing from this list is not rejected — it is filed
    # away as a metadata field and silently does nothing, which is worse than an
    # error. Anything added to UploadRequest belongs here too.
    if "metadata" not in data or not isinstance(data.get("metadata"), dict):
        # Extract upload options before wrapping
        node_id = data.pop("node_id", None)
        repository = data.pop("repository", "staging")
        check_duplicates = data.pop("check_duplicates", True)
        start_workflow = data.pop("start_workflow", True)
        source = data.pop("source", None)
        preview_url = data.pop("preview_url", None)
        screenshot_method = data.pop("screenshot_method", "playwright") or "playwright"
        write_extended_data = data.pop("write_extended_data", True)
        extended_text = data.pop("extended_text", None)
        return_full_node = data.pop("return_full_node", False)
        collection_id = data.pop("collection_id", None)
        workflow_steps = data.pop("workflow_steps", None)
        workflow_comment = data.pop("workflow_comment", None)
        workflow_receiver = data.pop("workflow_receiver", None)
        data = {
            "metadata": data,
            "node_id": node_id,
            "repository": repository,
            "check_duplicates": check_duplicates,
            "start_workflow": start_workflow,
            "return_full_node": return_full_node,
            "source": source,
            "preview_url": preview_url,
            "screenshot_method": screenshot_method,
            "write_extended_data": write_extended_data,
            "extended_text": extended_text,
            "collection_id": collection_id,
            "workflow_steps": workflow_steps,
            "workflow_comment": workflow_comment,
            "workflow_receiver": workflow_receiver,
        }
    else:
        carry_schema_markers(data)

    # Validate with Pydantic model
    try:
        req = UploadRequest(**data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Validation error: {str(e)}")

    repo_service = require_repository_service()

    # Extract context/version from metadata (before potential unwrapping)
    context = req.metadata.get("contextName", "default")
    version = req.metadata.get("schemaVersion", "latest")

    # Normalize: if web component sent nested format (metadata.metadata),
    # keep the original wrapper for _write_extended_fields but work with
    # unwrapped fields for source override and wwwurl detection
    _inner = (
        req.metadata.get("metadata")
        if isinstance(req.metadata.get("metadata"), dict)
        else None
    )
    _fields = _inner if _inner is not None else req.metadata

    # Apply source override if provided
    if req.source:
        _fields["ccm:oeh_publisher_combined"] = req.source
        # Also write to nested metadata if present so _extract_metadata_fields picks it up
        if _inner is not None:
            _inner["ccm:oeh_publisher_combined"] = req.source

    # ── Start screenshot capture async (runs in parallel with metadata upload) ──
    screenshot_task = None
    preview_url = req.preview_url

    # Auto-detect preview_url from metadata if not explicitly provided
    if not preview_url:
        wwwurl = _fields.get("ccm:wwwurl")
        if isinstance(wwwurl, list):
            wwwurl = wwwurl[0] if wwwurl else None
        if isinstance(wwwurl, str) and wwwurl.startswith("http"):
            preview_url = wwwurl

    if preview_url:
        screenshot_service = get_screenshot_service()
        logger.info(
            f"📸 Starting async screenshot capture: {preview_url} (method: {req.screenshot_method.value})"
        )
        screenshot_task = asyncio.create_task(
            screenshot_service.capture(
                url=preview_url,
                method=req.screenshot_method.value,
            )
        )

    # ── Upload metadata (runs in parallel with screenshot) ──
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
        )
    except BaseException:
        # As in /generate: nobody awaits the capture any more.
        if screenshot_task:
            screenshot_task.cancel()
        raise

    # A handed-over node that may not be written is refused like in /nodes.
    error_code = result.get("error_code")
    if error_code in _SAVE_NODE_ERROR_STATUS:
        if screenshot_task:
            screenshot_task.cancel()
        raise HTTPException(
            status_code=_SAVE_NODE_ERROR_STATUS[error_code],
            detail=result.get("error", "Node kann nicht bearbeitet werden"),
        )

    # ── Upload screenshot as preview (if screenshot was started and node was created) ──
    preview_status = None
    if (
        screenshot_task
        and result.get("success")
        and result.get("node", {}).get("nodeId")
    ):
        node_id = result["node"]["nodeId"]
        try:
            screenshot_result = await screenshot_task
            logger.info(
                f"📸 Screenshot ready: {screenshot_result.size_bytes} bytes ({screenshot_result.capture_time_ms}ms)"
            )

            # Upload as preview
            from .services.repository_service import _get_repository_config

            config = _get_repository_config()
            base_url = config["base_url"]
            if base_url:
                upload_url = (
                    f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/preview"
                    f"?mimetype={screenshot_result.mimetype}&createVersion=true"
                )
                async with httpx.AsyncClient(
                    timeout=45.0, event_hooks=OWN_SERVICE_HOOKS
                ) as client:
                    resp = await client.post(
                        upload_url,
                        headers={"Authorization": repo_service._auth_header},
                        files={
                            "image": (
                                "screenshot.png",
                                screenshot_result.image_bytes,
                                screenshot_result.mimetype,
                            )
                        },
                    )
                    if resp.status_code in (200, 201, 204):
                        logger.info(f"✅ Preview uploaded to node {node_id}")
                        preview_status = {
                            "success": True,
                            "method": screenshot_result.method,
                            "capture_time_ms": screenshot_result.capture_time_ms,
                            "size_bytes": screenshot_result.size_bytes,
                        }
                    else:
                        logger.warning(f"⚠️ Preview upload failed: {resp.status_code}")
                        preview_status = {
                            "success": False,
                            "error": f"HTTP {resp.status_code}",
                        }
        except Exception as e:
            logger.warning(f"⚠️ Screenshot/preview failed: {type(e).__name__}: {e}")
            preview_status = {"success": False, "error": str(e)}
    elif screenshot_task:
        # Cancel screenshot if upload failed
        screenshot_task.cancel()

    # Convert nested node dict to UploadedNodeInfo if present
    node_info = None
    if result.get("node"):
        node_info = UploadedNodeInfo(**result["node"])

    # Convert field errors to Pydantic models
    field_errors = None
    raw_errors = result.get("field_errors")
    if raw_errors:
        field_errors = [FieldUploadError(**e) for e in raw_errors]

    collections = None
    if result.get("collections") is not None:
        collections = [CollectionResult(**c) for c in result["collections"]]

    workflow = None
    if result.get("workflow") is not None:
        workflow = [WorkflowStepResult(**s) for s in result["workflow"]]

    # Add preview info to result
    if preview_status:
        result["preview"] = preview_status

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


# ============================================================================
# Node Write Endpoint (create or update)
# ============================================================================


# HTTP status per refusal reason from RepositoryService.save_node. Everything
# here is a statement about the node itself, not about the request — the body
# would be accepted unchanged for a different node.
_SAVE_NODE_ERROR_STATUS = {
    "node_too_old": 403,
    "node_age_unknown": 403,
    "node_not_found": 404,
}


@app.post(
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
und mit `404`, wenn der Node nicht existiert oder der Dienst keinen Zugriff hat.

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
als Metadatenfeld geschrieben. Erlaubt sind PNG, JPEG, GIF, WebP und SVG bis
10 MB; der Medientyp wird aus der Data-URL, dem `Content-Type` der Quelle oder
notfalls den ersten Bytes bestimmt.

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
        raise HTTPException(
            status_code=422, detail=f"Ungültiger Request-Body: {e.errors()}"
        ) from e

    repo_service = require_repository_service()

    context = req.metadata.get("contextName", "default")
    version = req.metadata.get("schemaVersion", "latest")

    # Handover first, release second — the order the editorial process runs in.
    workflow_steps = []
    if req.start_review_workflow:
        workflow_steps.append(DEFAULT_WORKFLOW_STATUS)
    if req.start_quality_workflow:
        workflow_steps.append(QUALITY_WORKFLOW_STATUS)

    result = await repo_service.save_node(
        metadata=req.metadata,
        node_id=req.node_id,
        context=context,
        version=version,
        write_extended_data=req.write_extended_data,
        extended_text=req.extended_text,
        collection_ids=req.collection_id,
        workflow_steps=workflow_steps,
        preview=req.preview,
    )

    error_code = result.get("error_code")
    if error_code in _SAVE_NODE_ERROR_STATUS:
        raise HTTPException(
            status_code=_SAVE_NODE_ERROR_STATUS[error_code],
            detail=result.get("error", "Node kann nicht bearbeitet werden"),
        )

    node_info = None
    if result.get("node"):
        node_info = UploadedNodeInfo(**result["node"])

    field_errors = None
    if result.get("field_errors"):
        field_errors = [FieldUploadError(**e) for e in result["field_errors"]]

    collections = None
    if result.get("collections") is not None:
        collections = [CollectionResult(**c) for c in result["collections"]]

    workflow = None
    if result.get("workflow") is not None:
        workflow = [WorkflowStepResult(**s) for s in result["workflow"]]

    return SaveNodeResponse(
        success=result.get("success", False),
        node=node_info,
        node_full=result.get("node_full"),
        node_created=result.get("node_created"),
        error=result.get("error"),
        step=result.get("step"),
        fields_written=result.get("fields_written"),
        fields_skipped=result.get("fields_skipped"),
        schema_used=result.get("schema_used"),
        repo_fields_available=result.get("repo_fields_available"),
        field_errors=field_errors,
        preview=result.get("preview"),
        collections=collections,
        workflow=workflow,
        discarded_node=result.get("discarded_node"),
    )


# ============================================================================
# Node Creation Endpoint
# ============================================================================


@app.post(
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
        metadata=data, check_duplicates=req.check_duplicates
    )

    if not result.get("success") and not result.get("duplicate"):
        raise HTTPException(
            status_code=400,
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


def require_node_id(node_id: str) -> None:
    """
    Reject a path parameter that is not an edu-sharing node id.

    The value is interpolated into the repository URL the service builds, and
    that request carries the service account's credentials — an id containing
    '/' would steer it at a different endpoint.
    """
    if not is_valid_node_id(node_id):
        raise HTTPException(
            status_code=400,
            detail="Ungültige node_id — erwartet wird eine Node-UUID, z.B. '3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9'.",
        )


@app.post(
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
        raise HTTPException(
            status_code=422, detail=f"Ungültiger Request-Body: {e.errors()}"
        ) from e

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


# ============================================================================
# Screenshot Endpoint
# ============================================================================


@app.post(
    "/screenshot",
    response_model=ScreenshotResponse,
    summary="Screenshot einer Webseite erstellen",
    description="""Erstellt einen Screenshot einer Webseite.

## Methoden

| Methode | Datenschutz | Geschwindigkeit | Beschreibung |
|---------|-------------|-----------------|-------------|
| **playwright** | ✅ Intern, keine Daten nach außen | ~3-5s | Lokaler Chromium-Browser (Standard) |
| **pageshot** | ✅ wie playwright | wie playwright | Legacy-Alias — nimmt ebenfalls lokal auf; die Antwort meldet `playwright` |

Nur öffentliche http(s)-Ziele werden aufgenommen; interne Adressen (Loopback,
private Netze, Link-Local) beantwortet der Dienst mit HTTP 400.

## Optional: Preview-Upload

Wenn `node_id` angegeben wird, wird der Screenshot direkt als Vorschaubild auf den edu-sharing Node hochgeladen.
""",
    tags=["Screenshot"],
)
async def take_screenshot(req: ScreenshotRequest):
    """Capture a screenshot and optionally upload as preview to edu-sharing node."""
    import base64

    screenshot_service = get_screenshot_service()

    try:
        result = await screenshot_service.capture(
            url=req.url,
            method=req.method.value,
            width=req.width,
            height=req.height,
            format=req.format,
            full_page=req.full_page,
            delay=req.delay,
        )
    except PublicUrlError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ScreenshotError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        # The text can name internal hosts and paths: log it, do not return it.
        logger.error(f"❌ Screenshot failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Screenshot failed") from e

    response = ScreenshotResponse(
        success=True,
        method=result.method,
        url=result.url,
        format=result.format,
        mimetype=result.mimetype,
        width=result.width,
        height=result.height,
        size_bytes=result.size_bytes,
        capture_time_ms=result.capture_time_ms,
        image_base64=base64.b64encode(result.image_bytes).decode(),
    )

    # Optional: upload as preview to node
    if req.node_id:
        repo_service = get_repository_service()
        if not repo_service:
            response.error = "Repository service not configured (missing credentials)"
            response.preview_uploaded = False
        else:
            # Upload the image just returned instead of capturing a second time.
            upload_result = await screenshot_service.upload_preview(
                result,
                node_id=req.node_id,
                auth_header=repo_service._auth_header,
            )
            response.preview_uploaded = upload_result.get("success", False)
            response.node_id = req.node_id
            if not upload_result.get("success"):
                response.error = upload_result.get("error")

    return response


# ============================================================================
# Upload Verification Endpoint
# ============================================================================


@app.post(
    "/upload/verify/{node_id}",
    response_model=VerifyResponse,
    summary="Upload-Ergebnis prüfen (SOLL/IST-Vergleich)",
    description="""Liest die Metadaten eines Nodes aus dem Repository und vergleicht sie optional mit den erwarteten Werten.

## Nutzung

**Nur lesen** (ohne Body): Gibt die aktuellen Metadaten des Nodes zurück.

**SOLL/IST-Vergleich** (mit Body): Vergleicht die erwarteten Metadaten (z.B. Output von `/generate`) mit den tatsächlich geschriebenen Werten.

## Diff-Status pro Feld

| Status | Bedeutung |
|--------|-----------|
| `match` | Wert stimmt überein |
| `mismatch` | Wert weicht ab (SOLL ≠ IST) |
| `missing_in_repo` | Feld wurde erwartet, ist aber nicht im Repository |
| `extra_in_repo` | Feld ist im Repository, war aber nicht erwartet |
| `not_written` | Feld war nicht repo-fähig (virtual:, schema:, oder kein repo_field) |

## Summary

Zählt die Anzahl der Felder pro Status-Kategorie.
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
async def verify_upload(node_id: str, request: Request):
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
            raise HTTPException(
                status_code=400, detail=f"Ungültiger Request-Body: {e.errors()}"
            ) from e

        expected_metadata = req.expected_metadata

        # Extract context/version from expected metadata if available
        if expected_metadata:
            context = expected_metadata.get("contextName", "default")
            version = expected_metadata.get("schemaVersion", "latest")

    result = await repo_service.verify_node(
        node_id=node_id,
        repository=repository,
        expected_metadata=expected_metadata,
        context=context,
        version=version,
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=502, detail=result.get("error", "Failed to verify node")
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
