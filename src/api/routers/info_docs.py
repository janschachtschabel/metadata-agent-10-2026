"""
The answers /docs shows for the read-only operations - /info/... and
/widget/...: the JSON of docs/API.md (audit API-15).

The long answers are shortened to a few entries; every entry shown is one of
the real answer (tests/test_openapi_response_examples.py).
"""

from ..openapi import success_example

SCHEMATA_RESPONSES = success_example(
    {
        "contexts": [
            {
                "name": "default",
                "display_name": "WLO/OEH Standard",
                "versions": ["1.8.1", "1.8.0", "2.0.0"],
                "default_version": "2.0.0",
            },
            {
                "name": "mds_oeh",
                "display_name": "MDS OEH",
                "versions": ["1.8.1", "1.8.0", "2.0.0"],
                "default_version": "2.0.0",
            },
        ],
        "default_context": "default",
    }
)

# Two of the schema files of default/2.0.0.
SCHEMAS_RESPONSES = success_example(
    [
        {
            "file": "core.json",
            "profile_id": "core:descriptive",
            "label": {"de": "core.json", "en": "core.json"},
            "groups": ["description", "typification", "quality", "ai_provenance"],
            "field_count": 27,
        },
        {
            "file": "event.json",
            "profile_id": "type:event",
            "label": {"de": "Veranstaltung", "en": "Event"},
            "groups": [
                "event-typification",
                "schedule",
                "attendance",
                "roles",
                "education",
                "license",
            ],
            "field_count": 22,
        },
    ]
)

# event.json of default/2.0.0 with one prefix, one group, one field and three
# keys of the template.
SCHEMA_DEFINITION_RESPONSES = success_example(
    {
        "profileId": "type:event",
        "version": "2.0.0",
        "@context": {"oeh": "http://w3id.org/openeduhub/vocabs/"},
        "groups": [
            {
                "id": "schedule",
                "icon": "calendar_today",
                "label": {"de": "Termin & Status", "en": "Schedule & Status"},
            }
        ],
        "fields": [
            {
                "id": "ccm:oeh_event_begin",
                "group": "schedule",
                "label": {"de": "Start (Datum/Zeit)", "en": "Start (Date/Time)"},
                "description": {
                    "de": "z.B. 2026-09-15T20:00:00.000Z",
                    "en": "e.g. 2026-09-15T20:00:00.000Z",
                },
                "prompt": {
                    "de": "Extrahiere das Startdatum und ggf. die Startzeit der Veranstaltung. Konvertiere IMMER ins vollständige ISO 8601-Format mit UTC-Zeitzone: YYYY-MM-DDTHH:mm:ss.000Z. Wenn nur ein Datum ohne Uhrzeit angegeben ist, verwende T00:00:00.000Z. Achte auf deutsche Datumsformate (TT.MM.JJJJ) und wandle korrekt um. Beispiele: '2026-09-15T20:00:00.000Z' (bei '15.09.2026, 20:00 Uhr'), '2026-03-01T00:00:00.000Z' (bei '1. März 2026'), '2026-10-10T09:30:00.000Z' (bei '10.10.2026 ab 9:30').",
                    "en": "Extract the start date and optionally start time of the event. ALWAYS convert to full ISO 8601 format with UTC timezone: YYYY-MM-DDTHH:mm:ss.000Z. If only a date without time is given, use T00:00:00.000Z. Watch for European date formats (DD.MM.YYYY) and convert correctly. Examples: '2026-09-15T20:00:00.000Z' (for 'Sep 15, 2026 at 8pm'), '2026-03-01T00:00:00.000Z' (for 'March 1, 2026'), '2026-10-10T09:30:00.000Z' (for '10/10/2026 starting 9:30am').",
                },
                "system": {
                    "path": "ccm:oeh_event_begin",
                    "uri": "ccm:oeh_event_begin",
                    "datatype": "datetime",
                    "multiple": False,
                    "required": False,
                    "ask_user": True,
                    "ai_fillable": True,
                    "index": {"keyword": True},
                    "validation": {
                        "pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{3}Z$"
                    },
                    "normalization": {"trim": True},
                    "repo_field": True,
                },
            }
        ],
        "output_template": {
            "oeh:eventType": [],
            "ccm:oeh_event_begin": "",
            "ccm:oeh_event_end": "",
        },
    }
)

# One variant, layout and event, two attributes, three example pages.
WIDGET_INFO_RESPONSES = success_example(
    {
        "name": "metadata-agent-canvas",
        "version": "4.0.4",
        "description": "Angular Web Component zur Anzeige und Bearbeitung von Metadaten. Kann als <metadata-agent-canvas> Tag in beliebige Webanwendungen eingebettet werden. Die URLs dieser Antwort sind relativ zur Adresse von /widget/info; {AGENT_URL} in den Snippets steht für die Basis-URL des Dienstes.",
        "dist_base_url": "dist",
        "scripts": {
            "required": ["dist/runtime.js", "dist/polyfills.js", "dist/main.js"],
            "styles": ["dist/styles.css"],
            "fonts": [],
        },
        "variants": {
            "minimal": {
                "description": "Kompakte, randlose Variante ohne Statusbar. Für Einbettung in bestehende Formulare oder Sidebars.",
                "example_url": "examples/minimal.html",
                "snippet": '<link rel="stylesheet" href="{AGENT_URL}/widget/dist/styles.css">\n<script src="{AGENT_URL}/widget/dist/runtime.js" defer></script>\n<script src="{AGENT_URL}/widget/dist/polyfills.js" defer></script>\n<script src="{AGENT_URL}/widget/dist/main.js" defer></script>\n\n<metadata-agent-canvas\n  api-url="{AGENT_URL}"\n  layout="clean"\n  show-input-area="true"\n  show-status-bar="false"\n  show-floating-controls="false">\n</metadata-agent-canvas>',
            }
        },
        "layouts": {
            "default": "Vollständige Bearbeitung — Eingabe, Statusbar, Floating Controls, Footer."
        },
        "layout_aliases": {"dialog": ["modal", "review", "redaktion"]},
        "attributes": {
            "layout": "Layout-Variante, siehe 'layouts'. Aliase siehe 'layout_aliases'.",
            "context-name": "Schema-Kontext: 'default' oder 'mds_oeh'.",
        },
        "events": {
            "metadataSubmit": "Beim Absenden/Speichern. event.detail enthält die vollständigen Metadaten."
        },
        "incoming_events": {
            "plugin-extract": "Extraktion von außen anstoßen: canvas.dispatchEvent(new CustomEvent('plugin-extract', {detail: {text, url, inputMode, reset}}))."
        },
        "examples": {
            "full": "examples/full.html",
            "detail": "examples/detail.html",
            "minimal": "examples/minimal.html",
        },
        "example_data": {"metadata_json": "examples/metadata-2026-02-11.json"},
    }
)

# Two of the groups of de.json.
WIDGET_TEXTS_RESPONSES = success_example(
    {
        "COMMON": {
            "CANCEL": "Abbrechen",
            "CLOSE": "Schließen",
            "CONFIRM": "Bestätigen",
        },
        "LANGUAGE": {
            "SWITCH_TO_ENGLISH": "Switch to English",
            "SWITCH_TO_GERMAN": "Zu Deutsch wechseln",
        },
    }
)
