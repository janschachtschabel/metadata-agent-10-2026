"""
The answers /docs shows for /extract-field and /detect-content-type: the JSON
of docs/API.md (audit API-15).
"""

from ..openapi import success_example

EXTRACT_FIELD_RESPONSES = success_example(
    {
        "field_id": "ccm:oeh_event_begin",
        "field_label": "Start (Datum/Zeit)",
        "value": "2026-03-20T00:00",
        "raw_value": "2026-03-20",
        "previous_value": "2026-03-15T09:00",
        "changed": True,
        "normalized": True,
        "context": "default",
        "version": "2.0.0",
        "schema_file": "event.json",
        "processing": {
            "llm_provider": "b-api-openai",
            "llm_model": "gpt-6-luna",
            "processing_time_ms": 450,
            "warnings": [],
        },
    }
)

# `available` is shortened to one of the content types.
DETECT_CONTENT_TYPE_RESPONSES = success_example(
    {
        "detected": {
            "schema_file": "event.json",
            "uri": "http://w3id.org/openeduhub/vocabs/contentTypes/event",
            "profile_id": "type:event",
            "label": {"de": "Veranstaltung", "en": "Event"},
            "confidence": "high",
        },
        "available": [
            {
                "schema_file": "learning_material.json",
                "uri": "http://w3id.org/openeduhub/vocabs/contentTypes/learning_material",
                "profile_id": "type:learning_material",
                "label": {"de": "Lernmaterial", "en": "Learning Material"},
            }
        ],
        "context": "default",
        "version": "2.0.0",
        "processing_time_ms": 800,
        "method": "llm",
        "warnings": [],
    }
)
