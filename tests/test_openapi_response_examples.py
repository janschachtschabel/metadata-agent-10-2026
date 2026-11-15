"""
Every operation shows an example of its success answer in /docs, and it is the
answer the API reference shows (audit 13.11., API-15; plan 14.11., K-13).

For the build an example is free text, like the request examples
(test_documented_examples_validate.py): nothing else notices when a response
model changes or one of the documents is edited on its own. So each example is
held against the response model of its route - or, where the route answers a
free-form object, against the real answer it shortens - and against the JSON
block after "Response `200`" of the operation in docs/API.md and
docs/API.de.md.

A program cannot tell a personal name from other text. The last block checks
what it can: hosts, mail addresses, the keys under which an answer of the
repository names people, and anything that looks like a key or token.
"""

import functools
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from src.main import app
from src.models.schemas import ProcessingInfo
from src.services.metadata_generation import load_extraction_fields
from src.utils.schema_loader import (
    get_available_schemas,
    get_content_type_uri,
    get_schema_fields,
    load_schema,
)

DOCS = Path(__file__).resolve().parent.parent / "docs"
METHODS = {"get", "put", "post", "delete", "patch"}

OPERATIONS: dict[str, dict] = {
    f"{method.upper()} {path}": operation
    for path, operations in app.openapi()["paths"].items()
    for method, operation in operations.items()
    if method in METHODS
}

# What each route declares as its answer; None where it returns what it built.
# Effective routes: included routers are one entry in app.routes.
RESPONSE_MODELS: dict[str, Any] = {
    f"{method} {route.path}": route.response_model
    for route in iter_route_contexts(app.routes)
    if getattr(route, "include_in_schema", False)
    for method in route.methods
}

GENERATE = "POST /generate"
UPLOAD = "POST /upload"


def _media(operation: str) -> dict:
    return OPERATIONS[operation]["responses"]["200"]["content"]["application/json"]


def _examples(operation: str) -> dict[str, Any]:
    """The success examples of an operation by name; a single one is named ''."""
    media = _media(operation)
    if "example" in media:
        return {"": media["example"]}
    return {name: entry["value"] for name, entry in media.get("examples", {}).items()}


EXAMPLES = [
    (operation, name, example)
    for operation in OPERATIONS
    for name, example in _examples(operation).items()
]
EXAMPLE_IDS = [f"{op}:{name}" if name else op for op, name, _ in EXAMPLES]


# ------------------------------------------------------------ every operation


def test_there_are_operations_to_check_at_all():
    assert len(OPERATIONS) >= 16
    assert set(OPERATIONS) == set(RESPONSE_MODELS)


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_every_operation_shows_an_example_of_its_success_answer(operation):
    media = _media(operation)

    assert _examples(operation), "kein Beispiel für die Antwort 200"
    # OpenAPI: `example` and `examples` exclude each other.
    assert not ("example" in media and "examples" in media)


@pytest.mark.parametrize("operation", sorted(OPERATIONS))
def test_an_example_adds_nothing_else_to_the_documented_answer(operation):
    """The examples are all this adds to the document (FIX-RULES: OpenAPI stays)."""
    answer = OPERATIONS[operation]["responses"]["200"]

    assert set(answer) == {"description", "content"}
    assert set(answer["content"]) == {"application/json"}
    assert set(_media(operation)) <= {"schema", "example", "examples"}
    for entry in _media(operation).get("examples", {}).values():
        assert set(entry) <= {"summary", "description", "value"}


def test_generate_shows_a_complete_and_a_partial_answer():
    """The two answers a caller has to tell apart behind the same 200."""
    examples = _examples(GENERATE)

    assert {name: e["processing"]["status"] for name, e in examples.items()} == {
        "vollstaendig": "success",
        "teilergebnis": "partial",
    }
    assert examples["vollstaendig"]["processing"]["errors"] == []
    assert examples["teilergebnis"]["processing"]["errors"]


def test_upload_shows_a_written_record_and_a_duplicate():
    """A duplicate is a 200 as well: `success: false`, nothing written."""
    examples = _examples(UPLOAD)

    assert list(examples) == ["geschrieben", "dublette"]
    assert examples["geschrieben"]["success"] is True
    assert not examples["geschrieben"].get("duplicate")
    assert examples["dublette"]["success"] is False
    assert examples["dublette"]["duplicate"] is True


# ------------------------------------------------- against the response model

MODELLED = [
    (operation, name, example)
    for operation, name, example in EXAMPLES
    if RESPONSE_MODELS[operation] is not None
]


@pytest.mark.parametrize(
    "operation, name, example",
    MODELLED,
    ids=[f"{op}:{name}" if name else op for op, name, _ in MODELLED],
)
def test_an_example_is_an_answer_its_response_model_describes(operation, name, example):
    adapter = TypeAdapter(RESPONSE_MODELS[operation])

    answer = adapter.validate_python(example, strict=True)

    # The models drop a key they do not know and would convert a value of
    # another kind: an example may rely on neither.
    assert adapter.dump_python(answer, exclude_unset=True, by_alias=True) == example


HEADER = [
    "contextName",
    "schemaVersion",
    "metadataset",
    "metadataset_uri",
    "language",
    "exportedAt",
]
ENVELOPE = {"_origins", "_source_text", "preview_image_url", "processing"}


@pytest.mark.parametrize("name", sorted(_examples(GENERATE)))
def test_a_generate_example_is_the_flat_record_the_endpoint_builds(name):
    """
    GenerateResponse is open for the metadata fields, so it accepts any key.
    The record itself is built in generate.py (_flat_response): header first,
    `processing` last, in between field ids of the schema the header names.
    """
    record = _examples(GENERATE)[name]
    context, version, schema_file = (
        record["contextName"],
        record["schemaVersion"],
        record["metadataset"],
    )
    known = {
        field["id"]
        for file in ("core.json", schema_file)
        for field in get_schema_fields(context, version, file)
    }
    fields = set(record) - set(HEADER) - ENVELOPE
    processing = record["processing"]

    assert list(record)[:6] == HEADER
    assert list(record)[-1] == "processing"
    assert record["metadataset_uri"] == get_content_type_uri(
        schema_file, context, version
    )
    assert fields and fields <= known
    assert fields <= set(record["_origins"]) <= known
    assert set(record["_origins"].values()) <= {"ai", "user"}
    # `processing` is open as well; an example names only what is described.
    assert set(processing) <= set(ProcessingInfo.model_fields)
    assert processing["status"] in {"success", "partial"}  # never `failed` in a 200
    assert processing["success"] is True
    assert processing["fields_total"] == len(
        load_extraction_fields(context, version, schema_file, True, [])
    )
    assert (
        processing["fields_extracted"] + processing["fields_failed"]
        <= processing["fields_total"]
    )


# -------------------------------------------- free-form answers: the real one


def _schema_definition(example: dict) -> dict:
    version = example["version"]
    schema_file = next(
        schema["file"]
        for schema in get_available_schemas("default", version)
        if schema["profile_id"] == example["profileId"]
    )
    return load_schema("default", version, schema_file)


def _widget_info(example: dict) -> dict:
    real = TestClient(app).get("/widget/info").json()
    # The version moves with every release; the example need not.
    return {**real, "version": example["version"]}


def _widget_texts(example: dict) -> dict:
    return TestClient(app).get("/widget/i18n/de.json").json()


# Per operation without a response model: the answer its example shortens, and
# whether the example names every top-level key of it.
REAL_ANSWER: dict[str, tuple[Callable[[dict], dict], bool]] = {
    "GET /info/schema/{context}/{version}/{schema_file}": (_schema_definition, True),
    "GET /widget/info": (_widget_info, True),
    "GET /widget/i18n/{lang}.json": (_widget_texts, False),
}


def _deviations(example: Any, real: Any, where: str = "$") -> list[str]:
    """Where a shortened example says something the real answer does not."""
    if isinstance(example, dict) and isinstance(real, dict):
        problems = []
        for key, value in example.items():
            if key not in real:
                problems.append(f"{where}.{key}: steht nicht in der Antwort")
            else:
                problems += _deviations(value, real[key], f"{where}.{key}")
        return problems
    if isinstance(example, list) and isinstance(real, list):
        return [
            f"{where}[{index}]: kein solcher Eintrag in der Antwort"
            for index, item in enumerate(example)
            if all(_deviations(item, candidate) for candidate in real)
        ]
    return [] if example == real else [f"{where}: {example!r} statt {real!r}"]


def test_every_operation_without_a_model_is_held_against_its_real_answer():
    """An operation missing here would be checked against nothing."""
    free_form = {op for op, model in RESPONSE_MODELS.items() if model is None}

    assert free_form == set(REAL_ANSWER)


@pytest.mark.parametrize("operation", sorted(REAL_ANSWER))
def test_a_free_form_example_is_a_shortened_real_answer(operation):
    real_answer, names_every_key = REAL_ANSWER[operation]
    (example,) = _examples(operation).values()

    real = real_answer(example)

    assert _deviations(example, real) == []
    if names_every_key:
        assert set(example) == set(real)


# ------------------------------------------------------- the API reference

# How each language file marks the success answer of an operation.
MARKERS = {"API.md": "**Response `200`", "API.de.md": "**Antwort `200`"}
_OPERATION = re.compile(r"\b(GET|POST) (/[^\s`]*)")


@functools.cache
def _documented(file: str) -> dict[str, list[Any]]:
    """
    Per operation the JSON blocks that follow its marker, in reading order.

    The operation is the one its section heading names, or the marker itself
    where a section describes several (`GET /widget/info`).
    """
    blocks: dict[str, list[Any]] = {}
    operation = None
    marked = False
    fence: list[str] | None = None
    is_json = False
    for line in (DOCS / file).read_text(encoding="utf-8").splitlines():
        if fence is not None:
            if not line.startswith("```"):
                fence.append(line)
                continue
            if is_json and marked and operation:
                blocks.setdefault(operation, []).append(json.loads("\n".join(fence)))
            fence = None
        elif line.startswith("```"):
            fence, is_json = [], line.strip() == "```json"
        elif line.startswith("#"):
            named = _OPERATION.search(line)
            operation, marked = (" ".join(named.groups()) if named else None), False
        elif line.startswith(MARKERS[file]):
            named = _OPERATION.search(line)
            if named:
                operation = " ".join(named.groups())
            marked = True
    return blocks


def _same_json(documented: Any, example: Any, where: str) -> None:
    assert documented == example, where
    # Also the order of the keys: /docs and the reference read alike.
    assert json.dumps(documented) == json.dumps(example), f"{where}: Reihenfolge"


@pytest.mark.parametrize("file", sorted(MARKERS))
def test_the_reference_marks_an_answer_for_known_operations_only(file):
    assert set(_documented(file)) <= set(OPERATIONS)


@pytest.mark.parametrize(
    "file, operation",
    [(file, operation) for file in sorted(MARKERS) for operation in sorted(OPERATIONS)],
)
def test_the_reference_shows_the_example_of_the_operation(file, operation):
    """
    The first JSON block after the marker is the (first) example in /docs.

    A null in the block cannot match: get_openapi drops every None of the
    document, also inside an example (success_example in openapi.py).
    """
    documented = _documented(file)
    assert operation in documented, f"{file}: kein JSON-Block nach dem Marker"

    example = next(iter(_examples(operation).values()))

    _same_json(documented[operation][0], example, f"{file}, {operation}")


# The second block of a section and the named example it is: /generate shows
# the `processing` of a partial result, /upload the whole answer to a duplicate.
SECOND_BLOCK: dict[str, tuple[str, Callable[[dict], Any]]] = {
    GENERATE: ("teilergebnis", lambda example: example["processing"]),
    UPLOAD: ("dublette", lambda example: example),
}


@pytest.mark.parametrize(
    "file, operation",
    [(file, operation) for file in sorted(MARKERS) for operation in SECOND_BLOCK],
)
def test_the_reference_shows_the_second_named_example(file, operation):
    name, shown = SECOND_BLOCK[operation]
    blocks = _documented(file)[operation]
    assert len(blocks) >= 2, f"{file}, {operation}: zweiter JSON-Block fehlt"

    _same_json(blocks[1], shown(_examples(operation)[name]), f"{file}, {operation}")


# ------------------------------------------- nothing real inside an example

ALLOWED_HOSTS = {
    "example.org",
    "example.com",
    # The public OpenEduHub vocabulary and repository the documents name.
    "w3id.org",
    "repository.staging.openeduhub.net",
}
_URL_HOST = re.compile(r"https?://([^/\s\"'<>)]+)")
_BARE_HOST = re.compile(
    r"\b(?:[a-z0-9-]+\.)+(?:net|org|com|de|eu|io|ai|cloud)\b", re.IGNORECASE
)
_MAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SECRET_KEY = re.compile(r"api[-_ ]?key|passwor[dt]|secret|authorization|token", re.I)
_SECRET_VALUE = re.compile(r"\bBearer\s|\bBasic\s|\bsk-[A-Za-z0-9]|eyJ[\w-]{10,}")
# Where a node, its workflow history or a vCard name a person.
_PERSON_KEY = re.compile(
    r"creator|modifier|owner|author|contribut|editor|firstname|lastname|"
    r"mailbox|e-?mail|username|authorityname",
    re.IGNORECASE,
)


def _keys(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


def _texts(node: Any) -> Iterator[str]:
    """Every string of an example, keys included."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _texts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _texts(item)
    elif isinstance(node, str):
        yield node


@pytest.mark.parametrize("operation, name, example", EXAMPLES, ids=EXAMPLE_IDS)
def test_an_example_names_no_host_but_the_documented_ones(operation, name, example):
    hosts = {
        host.lower()
        for text in _texts(example)
        for host in _URL_HOST.findall(text) + _BARE_HOST.findall(text)
    }

    assert hosts <= ALLOWED_HOSTS


@pytest.mark.parametrize("operation, name, example", EXAMPLES, ids=EXAMPLE_IDS)
def test_an_example_holds_no_key_and_names_no_person(operation, name, example):
    keys = list(_keys(example))
    texts = list(_texts(example))

    assert [key for key in keys if _SECRET_KEY.search(key)] == []
    assert [text for text in texts if _SECRET_VALUE.search(text)] == []
    assert [key for key in keys if _PERSON_KEY.search(key)] == []
    assert [text for text in texts if _MAIL.search(text)] == []
