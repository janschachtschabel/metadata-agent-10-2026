"""The OpenAPI document: request models of raw-Request endpoints, body schemas."""

from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel

from ..models.schemas import (
    ErrorResponse,
    GenerateRequest,
    ValidateRequest,
    ExportMarkdownRequest,
    UploadRequest,
    WorkflowRequest,
    VerifyRequest,
    DetectContentTypeRequest,
    ExtractFieldRequest,
)


# Custom OpenAPI schema: inject request models that are referenced via $ref
# but not auto-registered because endpoints use raw Request instead of Pydantic params
def custom_openapi(app):
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
    models: list[type[BaseModel]] = [
        GenerateRequest,
        DetectContentTypeRequest,
        ExtractFieldRequest,
        ValidateRequest,
        ExportMarkdownRequest,
        UploadRequest,
        VerifyRequest,
        WorkflowRequest,
    ]
    for model in models:
        model_schema = model.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        # Extract $defs (sub-schemas like enums) and merge into top-level schemas
        defs = model_schema.pop("$defs", {})
        for def_name, def_schema in defs.items():
            schemas.setdefault(def_name, def_schema)
        schemas[model.__name__] = model_schema
    _document_errors(openapi_schema, schemas)
    _document_accepted_gaps(openapi_schema)
    app.openapi_schema = openapi_schema
    return openapi_schema


def _document_errors(openapi_schema: dict, schemas: dict) -> None:
    """
    Every operation answers errors as ErrorResponse (responses.py, audit API-03).

    One "4XX" and one "5XX" entry per operation instead of a list per endpoint
    that would drift from the code; FastAPI's own 422 (HTTPValidationError)
    describes a shape the API no longer sends and is replaced.
    """
    schemas["ErrorResponse"] = ErrorResponse.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    for name, sub_schema in schemas["ErrorResponse"].pop("$defs", {}).items():
        schemas.setdefault(name, sub_schema)
    error = {
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
            }
        }
    }
    for operations in openapi_schema.get("paths", {}).values():
        for operation in operations.values():
            responses = operation.setdefault("responses", {})
            responses.pop("422", None)
            responses["4XX"] = {"description": "Fehler der Anfrage", **error}
            responses["5XX"] = {
                "description": "Fehler des Dienstes oder eines abhängigen Dienstes",
                **error,
            }
    schemas.pop("HTTPValidationError", None)


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


# Endpoints that write to or read from the repository with the service account
# and check no rights of their own (audit S-13). Accepted because only the
# B-API proxy reaches the service (NetworkPolicy); UEBERGABE.md §3.
_SERVICE_ACCOUNT_NOTE = (
    "\n\n**Rechte:** Dieser Endpunkt prüft keine eigenen Rechte des Aufrufers "
    "und handelt mit dem Service-Konto des Agents ({what}). Das ist akzeptiert, "
    "weil nur der B-API-Proxy den Dienst erreicht (NetworkPolicy, UEBERGABE.md §3)."
)
_READS_NODES = (
    "liest mit `input_source` `node_id` oder `node_url` auch nicht öffentliche "
    "Nodes und gibt deren Inhalt in das Ergebnis"
)
_ACCEPTED_GAPS = {
    (
        "/upload",
        "post",
    ): "schreibt; mit `node_id` auch in einen vorhandenen Node im Bearbeitungsfenster; "
    "`workflow_receiver` nimmt jeden Authority-Namen an",
    (
        "/nodes",
        "post",
    ): "schreibt; mit `node_id` in einen vorhandenen Node im Bearbeitungsfenster",
    ("/node", "post"): "legt Nodes im Eingangsordner an",
    (
        "/workflow/{node_id}",
        "post",
    ): "setzt Workflow-Status, auch Veröffentlichungs-Status wie 150_PUBLISH_IN_SEARCH, "
    "ohne Bearbeitungsfenster; `receiver` nimmt jeden Authority-Namen an; die Antwort "
    "enthält die Workflow-Historie mit den Benutzernamen der Bearbeitenden",
    (
        "/screenshot",
        "post",
    ): "setzt mit `node_id` das Vorschaubild eines Nodes im Bearbeitungsfenster",
    ("/upload/verify/{node_id}", "post"): "liest auch nicht öffentliche Nodes",
    ("/generate", "post"): _READS_NODES,
    ("/detect-content-type", "post"): _READS_NODES,
    ("/extract-field", "post"): _READS_NODES,
}


def _document_accepted_gaps(openapi_schema: dict) -> None:
    paths = openapi_schema.get("paths", {})
    for (path, method), what in _ACCEPTED_GAPS.items():
        operation = paths.get(path, {}).get(method)
        if operation is not None:
            operation["description"] = operation.get("description", "") + (
                _SERVICE_ACCOUNT_NOTE.format(what=what)
            )
