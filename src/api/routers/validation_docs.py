"""
The answers /docs shows for /validate and /export/markdown: the JSON of
docs/API.md (audit API-15).
"""

from ..openapi import success_example

VALIDATE_RESPONSES = success_example(
    {
        "valid": True,
        "schema_used": "event.json",
        "errors": [],
        "warnings": [
            {
                "field_id": "oeh:eventType",
                "message": "Value 'Worksho' not in vocabulary. Did you mean 'http://w3id.org/openeduhub/vocabs/eventType/workshop'?",
                "severity": "warning",
            }
        ],
        "coverage": 100.0,
    }
)

EXPORT_MARKDOWN_RESPONSES = success_example(
    {
        "markdown": "# Veranstaltung\n\n## Beschreibung\n\n**Titel:** Workshop KI in der Bildung\n\n## Typisierung\n\n**Schlagwörter:** KI, Bildung\n\n## Termin & Status\n\n**Start (Datum/Zeit):** 2026-03-15T09:00\n",
        "schema_used": "event.json",
    }
)
