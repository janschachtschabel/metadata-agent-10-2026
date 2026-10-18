"""The web component's texts and self-description (/widget/i18n, /widget/info)."""

import json
from pathlib import Path
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


from fastapi import APIRouter

from .. import deps


router = APIRouter()


WIDGET_LANGUAGES = {"de", "en"}


@router.get(
    "/widget/i18n/{lang}.json",
    summary="Widget UI-Übersetzungen",
    description="Liefert die UI-Übersetzungstexte für die Webkomponente. Unterstützte Sprachen: `de`, `en`.",
    tags=["Widget"],
)
async def widget_i18n(lang: str):
    """Serve i18n translation files for the web component."""
    # src/static, two packages up from src/api/routers/.
    i18n_dir = (
        Path(__file__).parent.parent.parent / "static" / "widget" / "assets" / "i18n"
    )
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


@router.get(
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
        "version": deps.settings.app_version,
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
