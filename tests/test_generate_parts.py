"""
/generate: the router holds the flow in three phases; its /docs texts and the
reading of the request live in modules of their own (audit 14.11.2026, Q-07).

generate.py had 694 lines, 215 of them the description and the examples of the
OpenAPI document, and `_generate` did everything in one function of 115 lines.
What the endpoint answers did not change; the screenshot helpers stay in the
router, because /upload chooses its URL differently and reports under its own
logger.
"""

import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException

from src import main
from src.api import generate_request
from src.api.routers import generate as generate_module
from src.api.routers.generate_docs import GENERATE_DESCRIPTION, GENERATE_OPENAPI_EXTRA
from src.models.schemas import GenerateRequest

ROUTER = Path(generate_module.__file__)


def _operation() -> dict:
    return main.app.openapi()["paths"]["/generate"]["post"]


def test_the_docs_show_the_description_of_the_docs_module():
    # custom_openapi() appends the note on the caller's rights.
    assert _operation()["description"].startswith(GENERATE_DESCRIPTION)
    assert GENERATE_DESCRIPTION.startswith("Generiert vollständige Metadaten")


def test_the_docs_show_the_examples_of_the_docs_module():
    documented = _operation()["requestBody"]["content"]
    examples = GENERATE_OPENAPI_EXTRA["requestBody"]["content"]

    assert set(documented) == set(examples) == {"application/json", "text/plain"}
    for media_type, content in examples.items():
        assert documented[media_type]["examples"] == content["examples"]
    assert list(examples["application/json"]["examples"]) == [
        "text_input",
        "url_input",
        "node_id_input",
        "node_url_input",
        "mit_existing_metadata",
        "anderer_provider",
    ]


def test_the_router_carries_no_prose_of_its_own():
    source = ROUTER.read_text(encoding="utf-8")

    assert "description=GENERATE_DESCRIPTION" in source
    assert "openapi_extra=GENERATE_OPENAPI_EXTRA" in source
    assert "## Input-Quellen" not in source
    assert source.count("\n") < 500


def test_the_run_has_three_phases():
    source = inspect.getsource(generate_module._generate)

    phases = ["_resolve(", "_run(", "_answer("]
    assert [source.index(phase) for phase in phases] == sorted(
        source.index(phase) for phase in phases
    )
    for phase in (generate_module._resolve, generate_module._run):
        assert inspect.iscoroutinefunction(phase)
    assert inspect.iscoroutinefunction(generate_module._answer)


def test_the_screenshot_helpers_stay_with_the_router_and_its_logger():
    for helper in (
        generate_module._start_screenshot,
        generate_module._screenshot_data_url,
    ):
        assert helper.__module__ == generate_module.__name__


class _Body:
    def __init__(self, body: bytes, content_type: str | None = None):
        self._body = body
        self.headers = {"content-type": content_type} if content_type else {}

    async def body(self) -> bytes:
        return self._body


async def test_a_plain_text_body_becomes_a_request_with_the_defaults():
    request = _Body("Ein Text\nüber zwei Zeilen".encode(), "text/plain; charset=utf-8")

    req = await generate_request.parse_generate_request(request)

    assert isinstance(req, GenerateRequest)
    assert req.text == "Ein Text\nüber zwei Zeilen"
    assert (req.context, req.version, req.schema_file) == ("default", "latest", "auto")


@pytest.mark.parametrize(
    "body, detail",
    [
        (b"{nicht json", "Invalid JSON"),
        (b"[1, 2]", "Body muss ein JSON-Objekt sein"),
        (b"\xff\xfe", "Request parsing error"),
    ],
)
async def test_an_unreadable_body_is_a_400(body, detail):
    with pytest.raises(HTTPException) as refused:
        await generate_request.parse_generate_request(_Body(body))

    assert refused.value.status_code == 400
    assert detail in refused.value.detail


async def test_without_max_workers_the_configured_default_applies():
    from src.api import deps

    req = await generate_request.parse_generate_request(_Body(b'{"text": "x"}'))
    named = await generate_request.parse_generate_request(
        _Body(b'{"text": "x", "max_workers": 3}')
    )

    assert req.max_workers == deps.settings.default_max_workers
    assert named.max_workers == 3


def test_a_long_text_beside_a_fetched_source_is_refused_not_dropped():
    limit = generate_request.MAX_CORRECTION_LENGTH
    as_text = GenerateRequest(text="x")
    with_url = GenerateRequest(input_source="url", source_url="https://example.org")

    assert f"länger als {limit} Zeichen" in generate_request.long_text_as_new_source(
        as_text
    )
    with pytest.raises(HTTPException) as refused:
        generate_request.long_text_as_new_source(with_url)
    assert refused.value.status_code == 400


def test_the_texts_of_a_first_run_and_of_a_correction():
    req = GenerateRequest(text="Bitte den Titel kürzen")

    text, kept, warnings = generate_request.texts_for_run(req, "Quelle", None, {})
    assert (text, kept, warnings) == ("Quelle", "Quelle", [])

    corrected, kept, warnings = generate_request.texts_for_run(
        req, "ignoriert", "Die erste Quelle", {"cclom:title": "Langer Titel"}
    )
    assert kept == "Die erste Quelle"
    assert "Bitte den Titel kürzen" in corrected and "Die erste Quelle" in corrected

    with pytest.raises(HTTPException) as refused:
        generate_request.texts_for_run(req, "   ", None, {})
    assert refused.value.status_code == 400
