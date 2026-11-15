"""
The answers /docs shows for the operations on the repository - /node, /nodes,
/upload, /workflow, /upload/verify - and for /screenshot, which stores the
preview of a node: the JSON of docs/API.md (audit API-15).

Optional fields without a value come back as null; like the reference, the
examples leave them out (success_example in openapi.py says why).
"""

from ..openapi import success_example, success_examples

_RENDER = "https://repository.staging.openeduhub.net/edu-sharing/components/render/"

CREATE_NODE_RESPONSES = success_example(
    {
        "success": True,
        "node": {
            "nodeId": "5443240c-43a2-4961-8324-0c43a22961dd",
            "title": "Bruchrechnung Klasse 6",
            "repositoryUrl": _RENDER + "5443240c-43a2-4961-8324-0c43a22961dd",
        },
    }
)

# An update; `node_full` is shortened.
SAVE_NODE_RESPONSES = success_example(
    {
        "success": True,
        "node_created": False,
        "schema_used": "learning_material.json",
        "repo_fields_available": 40,
        "fields_written": 1,
        "fields_skipped": 0,
        "skipped_fields": [],
        "node": {
            "nodeId": "177fba93-3885-479e-bfba-933885c79e1c",
            "repositoryUrl": _RENDER + "177fba93-3885-479e-bfba-933885c79e1c",
        },
        "node_full": {
            "ref": {"id": "177fba93-3885-479e-bfba-933885c79e1c"},
            "properties": {"cclom:general_keyword": ["Bruchrechnung", "Klasse 6"]},
        },
    }
)

UPLOAD_RESPONSES = success_examples(
    {
        "geschrieben": {
            "summary": "Datensatz geschrieben",
            "description": "Node angelegt, Metadaten geschrieben, Vorschaubild, "
            "Sammlung und Workflow erledigt. `error` auch bei `success: true` "
            "prüfen: ein späterer Schritt kann gescheitert sein.",
            "value": {
                "success": True,
                "node_created": True,
                "schema_used": "event.json",
                "repo_fields_available": 33,
                "fields_written": 12,
                "fields_skipped": 0,
                "skipped_fields": [],
                "node": {
                    "nodeId": "3f2b8c1e-7a4d-4e6f-9b0a-1c2d3e4f5a6b",
                    "title": "Workshop KI in der Bildung",
                    "description": "Ein Workshop über den Einsatz von KI im Unterricht.",
                    "wwwurl": "https://example.com/workshop",
                    "repositoryUrl": _RENDER + "3f2b8c1e-7a4d-4e6f-9b0a-1c2d3e4f5a6b",
                },
                "preview": {
                    "success": True,
                    "method": "playwright",
                    "capture_time_ms": 2800,
                    "size_bytes": 245000,
                },
                "collections": [
                    {
                        "collectionId": "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9",
                        "success": True,
                    }
                ],
                "workflow": [{"status": "200_tocheck", "success": True}],
            },
        },
        "dublette": {
            "summary": "Dublette",
            "description": "Die URL gibt es im Repository schon: ebenfalls "
            "`200`, mit `success: false`. Nichts wurde geschrieben, `node` ist "
            "der vorhandene Node.",
            "value": {
                "success": False,
                "duplicate": True,
                "node": {
                    "nodeId": "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                    "title": "Workshop KI in der Bildung",
                    "wwwurl": "https://example.com/workshop",
                    "repositoryUrl": _RENDER + "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
                },
                "error": 'URL existiert bereits: "Workshop KI in der Bildung"',
            },
        },
    }
)

# Each entry of the real `history` also names the acting user.
WORKFLOW_RESPONSES = success_example(
    {
        "success": True,
        "nodeId": "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9",
        "steps": [{"status": "140_ELEMENT_LEGALLY_APPROVED", "success": True}],
        "current_status": "140_ELEMENT_LEGALLY_APPROVED",
        "history": [
            {
                "status": "140_ELEMENT_LEGALLY_APPROVED",
                "comment": "Redaktionell geprüft",
                "time": 1786343291000,
            }
        ],
        "repositoryUrl": _RENDER + "3039bdb2-f51f-4cc8-b1d9-3fb6b0ffc1d9",
    }
)

# With `expected_metadata`: one field of each kind of finding.
VERIFY_RESPONSES = success_example(
    {
        "success": True,
        "node_id": "3f2b8c1e-7a4d-4e6f-9b0a-1c2d3e4f5a6b",
        "actual_metadata": {
            "cclom:title": "Workshop KI in der Bildung",
            "ccm:oeh_lrt": "http://w3id.org/openeduhub/vocabs/new_lrt/does-not-exist",
        },
        "unresolved": [
            {
                "field_id": "ccm:oeh_lrt",
                "value": ["http://w3id.org/openeduhub/vocabs/new_lrt/does-not-exist"],
                "known": False,
                "reason": "leeres _DISPLAYNAME — der Wert steht in keinem Vokabular",
            }
        ],
        "diff": [
            {
                "field_id": "cclom:general_keyword",
                "status": "missing_in_repo",
                "expected": ["KI", "Bildung"],
            },
            {
                "field_id": "ccm:oeh_lrt",
                "status": "extra_in_repo",
                "actual": "http://w3id.org/openeduhub/vocabs/new_lrt/does-not-exist",
            },
            {
                "field_id": "cclom:title",
                "status": "match",
                "expected": "Workshop KI in der Bildung",
                "actual": "Workshop KI in der Bildung",
            },
        ],
        "summary": {
            "match": 1,
            "mismatch": 0,
            "missing_in_repo": 1,
            "extra_in_repo": 1,
            "not_written": 0,
            "unresolved": 0,
        },
    }
)

# Without `node_id`; the image is cut short.
SCREENSHOT_RESPONSES = success_example(
    {
        "success": True,
        "method": "playwright",
        "url": "https://example.com",
        "format": "png",
        "mimetype": "image/png",
        "width": 1920,
        "height": 1080,
        "size_bytes": 245000,
        "capture_time_ms": 2800,
        "image_base64": "iVBORw0KGgo…",
    }
)
