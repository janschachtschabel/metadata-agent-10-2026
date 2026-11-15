"""
What /docs says about /generate: the description, the request examples and
the examples of the answer (the JSON of docs/API.md, audit API-15).
"""

from typing import Any

from ..openapi import success_examples

GENERATE_DESCRIPTION = """Generiert vollständige Metadaten aus Text, URL oder Repository-Node mittels LLM.

## Input-Quellen (input_source)

| Wert | Beschreibung | Benötigte Felder |
|------|-------------|------------------|
| `text` | Direkter Text als Eingabe (Standard) | `text` |
| `url` | Text von URL via Crawler abrufen | `source_url`, optional `extraction_method` |
| `node_id` | Metadaten + hinterlegte Volltexte von Repository-Node | `node_id` |
| `node_url` | Repository-Node + Crawler-Fallback (URL aus ccm:wwwurl) | `node_id` (source_url optional, wird aus Metadaten geholt) |

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

- **screenshot_method**: `playwright` (lokal) oder der Legacy-Alias `pageshot` (nimmt ebenfalls lokal auf, kein externer Dienst). Wenn nicht gesetzt oder leer: kein Screenshot. Andere Werte: `422`.
- **preview_url**: URL für Screenshot (optional). Wenn nicht gesetzt: wird aus `source_url` oder bestehenden Metadaten (`ccm:wwwurl`) ermittelt.
- Antwort enthält `preview_image_url` als Base64 Data-URL wenn Screenshot erfolgreich.

## LLM-Optionen

- **llm_provider**: `b-api-openai` (Standard), `b-api-academiccloud`, `b-api-router`, `openai`
- **llm_model**: Ohne Angabe gilt der Default des Providers — `gpt-6-luna` (b-api-openai), `deepseek-v4-flash` (b-api-academiccloud), `openai/gpt-6-luna` (b-api-router, Routenname oder provider/modell), `gpt-4o-mini` (openai)
"""

GENERATE_OPENAPI_EXTRA: dict[str, Any] = {
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
}

_RECORD_HEADER: dict[str, Any] = {
    "contextName": "default",
    "schemaVersion": "2.0.0",
    "metadataset": "event.json",
    "metadataset_uri": "http://w3id.org/openeduhub/vocabs/contentTypes/event",
    "language": "de",
    "exportedAt": "2026-11-14T08:00:00.123456+00:00",
}
_SOURCE_TEXT = "Workshop KI in der Bildung am 15. März 2026 um 9 Uhr in Berlin."

GENERATE_RESPONSES = success_examples(
    {
        "vollstaendig": {
            "summary": "Vollständiges Ergebnis",
            "description": "`processing.status: success` – kein Feld ist aus "
            "technischem Grund gescheitert. Felder ohne Wert stehen nicht im "
            "Datensatz.",
            "value": {
                **_RECORD_HEADER,
                "cclom:title": "Workshop KI in der Bildung",
                "cclom:general_description": "Ein Workshop über den Einsatz von KI im Unterricht.",
                "cclom:general_keyword": ["KI", "Bildung", "Workshop"],
                "ccm:oeh_event_begin": "2026-03-15T09:00",
                "schema:location": [
                    {
                        "name": "Berlin",
                        "address": {
                            "streetAddress": "",
                            "postalCode": "",
                            "addressLocality": "Berlin",
                            "addressRegion": "Berlin",
                            "addressCountry": "DE",
                        },
                        "geo": {"latitude": 52.5200066, "longitude": 13.404954},
                    }
                ],
                "_origins": {
                    "cclom:title": "ai",
                    "cclom:general_description": "ai",
                    "cclom:general_keyword": "ai",
                    "ccm:oeh_event_begin": "ai",
                    "schema:location": "ai",
                },
                "_source_text": _SOURCE_TEXT,
                "processing": {
                    "status": "success",
                    "success": True,
                    "fields_extracted": 5,
                    "fields_failed": 0,
                    "fields_total": 47,
                    "processing_time_ms": 14200,
                    "llm_provider": "b-api-openai",
                    "llm_model": "gpt-6-luna",
                    "auth_source": "forwarded",
                    "content_type_detection": {
                        "method": "llm",
                        "confidence": "high",
                        "schema_file": "event.json",
                    },
                    "errors": [],
                    "warnings": [],
                },
            },
        },
        "teilergebnis": {
            "summary": "Teilergebnis am Zeitlimit",
            "description": "`processing.status: partial`, ebenfalls `200`: "
            "`processing.errors` nennt die Felder, die nicht mehr erschlossen "
            "wurden. Der Datensatz ist hier auf zwei der 31 erschlossenen "
            "Felder gekürzt.",
            "value": {
                **_RECORD_HEADER,
                "cclom:title": "Workshop KI in der Bildung",
                "ccm:oeh_event_begin": "2026-03-15T09:00",
                "_origins": {"cclom:title": "ai", "ccm:oeh_event_begin": "ai"},
                "_source_text": _SOURCE_TEXT,
                "processing": {
                    "status": "partial",
                    "success": True,
                    "fields_extracted": 31,
                    "fields_failed": 2,
                    "fields_total": 47,
                    "processing_time_ms": 295400,
                    "llm_provider": "b-api-openai",
                    "llm_model": "gpt-6-luna",
                    "auth_source": "forwarded",
                    "content_type_detection": {
                        "method": "preselected",
                        "schema_file": "event.json",
                    },
                    "errors": [
                        "Zeitlimit: 2 Feld(er) nicht mehr erschlossen, der KI-Dienst antwortete nicht rechtzeitig (ccm:competence, schema:offers)."
                    ],
                    "warnings": [],
                },
            },
        },
    }
)
