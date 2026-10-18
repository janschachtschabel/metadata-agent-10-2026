"""FastAPI application for metadata extraction."""

import logging
import mimetypes
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .utils.security_headers import SecurityHeadersMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from .api import deps
from .api.openapi import custom_openapi
from .api.responses import (
    UnexpectedErrorMiddleware,
    http_exception_handler,
    validation_exception_handler,
)
from .api.routers import (
    detect,
    docs,
    extract,
    generate,
    infra as infra_router,
    nodes,
    save_node,
    schemata,
    screenshot,
    upload,
    validation,
    verify,
    widget,
)
from .infra import InternalPortUnavailable, create_internal_app, start_internal_server
from .metrics import HttpMetricsMiddleware
from .services.llm_auth import ForwardedAuthorizationMiddleware
from .utils.body_limit import BodySizeLimitMiddleware
from .utils.logging_setup import configure_logging
from .utils.request_id import HEADER as REQUEST_ID_HEADER
from .utils.request_id import RequestIdMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup - with deps.settings, the same instance the endpoints answer with.
    llm_config = deps.settings.get_llm_config()
    logger.info(f"Starting {deps.settings.app_name} v{deps.settings.app_version}")
    logger.info(f"LLM Provider: {deps.settings.llm_provider}")
    logger.info(f"LLM Model: {llm_config['model']}")
    logger.info(f"LLM API Base: {llm_config['api_base']}")
    logger.info(f"B-API Base URL: {deps.settings.b_api_base_url}")
    logger.info(
        f"B-API Auth Mode: {deps.settings.b_api_auth_mode} "
        f"(B_API_KEY {'gesetzt' if deps.settings.b_api_key else 'nicht gesetzt'})"
    )
    logger.info(f"Text Extraction URL: {deps.settings.text_extraction_api_url}")
    logger.info(f"Repository URL: {deps.settings.repository_url}")
    for warning in deps.settings.config_warnings():
        logger.warning(f"⚠️ {warning}")

    # The gateway budget decides the real parallelism — printing the worker
    # default alone would say 20 where AcademicCloud runs 2.
    concurrent = llm_config["max_concurrent_requests"]
    rate = llm_config["requests_per_second"]
    effective = min(deps.settings.default_max_workers, concurrent)
    logger.info(
        f"Default Workers: {deps.settings.default_max_workers}"
        + (
            f" → {effective} (limit of {llm_config['limit_group']})"
            if effective < deps.settings.default_max_workers
            else ""
        )
    )
    logger.info(
        f"LLM Throughput: max {concurrent} in flight, "
        + (f"max {rate:g} req/s" if rate else "no rate cap")
    )

    # /health, /ready, /metrics on a port the ingress does not expose.
    stop_internal_server = None
    if deps.settings.internal_port:
        try:
            stop_internal_server = await start_internal_server(
                create_internal_app(get_internal_settings),
                deps.settings.internal_port,
                host=deps.settings.internal_host,
            )
        except InternalPortUnavailable as e:
            # The API itself keeps running; probes on the internal port fail.
            logger.error(f"❌ {e} Der Dienst läuft ohne internen Port weiter.")
        else:
            logger.info(
                f"Internal port: {deps.settings.internal_port} (/health, /ready, /metrics)"
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


def get_internal_settings():
    """The settings the internal port reports on - the same as the app's."""
    return deps.settings


# stdout, text or JSON lines (METADATA_AGENT_LOG_FORMAT / _LOG_LEVEL)
configure_logging(deps.settings.log_level, deps.settings.log_format)
app = FastAPI(
    title=deps.settings.app_name,
    version=deps.settings.app_version,
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


# Custom OpenAPI schema (src/api/openapi.py).
app.openapi = partial(custom_openapi, app)  # type: ignore[method-assign]  # FastAPI's documented override


# CORS middleware - origins configurable via METADATA_AGENT_CORS_ORIGINS env var
_cors_origins = (
    [o.strip() for o in deps.settings.cors_origins.split(",")]
    if deps.settings.cors_origins != "*"
    else ["*"]
)
# Every HTTPException and validation error in the API's error shape (API-03).
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# Innermost: an exception no endpoint handled becomes a JSON 500 that CORS and
# the request id still wrap (responses.py).
app.add_middleware(UnexpectedErrorMiddleware)

# Bound request bodies before an endpoint reads them into memory. Registered
# early, so CORS, metrics and the request id all wrap its 413.
app.add_middleware(
    BodySizeLimitMiddleware, max_bytes=lambda: deps.settings.max_request_bytes
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


# Outside everything but the security headers, so every response - errors
# included - carries X-Request-ID.
app.add_middleware(RequestIdMiddleware)

# Around everything, errors and 413 included (audit S-12).
app.add_middleware(SecurityHeadersMiddleware)


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

app.include_router(docs.router)

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

# Same order as the endpoints were defined before 3.0.0 - the spec lists them so.
for _router in (
    widget,
    infra_router,
    schemata,
    detect,
    extract,
    generate,
    validation,
    upload,
    save_node,
    nodes,
    screenshot,
    verify,
):
    app.include_router(_router.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
