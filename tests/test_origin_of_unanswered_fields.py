"""
A field the model was asked for and left empty is not the editor's (audit 14.11.2026, L-N).

final_origins promised 'ai' for a field that was asked for, but looked it up
in the answers: a field without an answer came back as origin "user" - in a
first run every empty field claimed that the editor had filled it. An empty
field that was asked for is "ai"; a value the caller brought and the model
did not replace stays "user", as before - and so does a field the model is
never asked for.
"""

import pytest

from src.services.metadata_generation import final_origins, load_extraction_fields
from src.services.metadata_service import MetadataService


def test_an_empty_field_that_was_asked_for_is_not_the_users():
    ordered = {"cclom:title": "Titel", "cclom:general_keyword": [], "ccm:wwwurl": ""}

    origins = final_origins(
        ordered,
        {"cclom:title": "ai"},
        {"cclom:title": "Titel"},
        asked={"cclom:title", "cclom:general_keyword", "ccm:wwwurl"},
    )

    assert origins == {
        "cclom:title": "ai",
        "cclom:general_keyword": "ai",
        "ccm:wwwurl": "ai",
    }


@pytest.mark.parametrize("empty", [None, "", [], {}, [""], [{}]])
def test_every_kind_of_empty_counts(empty):
    assert final_origins({"x:f": empty}, {}, {}, asked={"x:f"}) == {"x:f": "ai"}


def test_a_value_the_model_did_not_replace_stays_the_callers():
    # Asked for again, no answer: the caller's value is kept, and it is theirs.
    origins = final_origins({"cclom:title": "Alt"}, {}, {}, asked={"cclom:title"})

    assert origins == {"cclom:title": "user"}


def test_an_empty_field_that_was_not_asked_for_keeps_the_old_rule():
    assert final_origins({"x:f": ""}, {}, {}, asked=set()) == {"x:f": "user"}
    assert final_origins({"x:f": ""}, {}, {}) == {"x:f": "user"}


def test_a_recorded_origin_wins():
    origins = final_origins({"x:f": ""}, {"x:f": "user"}, {}, asked={"x:f"})

    assert origins == {"x:f": "user"}


async def _generate(monkeypatch, answers, **kwargs):
    service = MetadataService()

    async def extract_fields_parallel(fields, **rest):
        return {"values": dict(answers), "errors": [], "skipped": 0}

    monkeypatch.setattr(
        service.llm_service, "extract_fields_parallel", extract_fields_parallel
    )
    return await service.generate_metadata(
        text="Ein Text.",
        schema_file="event.json",
        version="2.0.0",
        enable_geocoding=False,
        **kwargs,
    )


async def test_a_first_run_marks_no_field_it_asked_for_as_the_users(monkeypatch):
    result = await _generate(monkeypatch, {"cclom:title": "Titel"})

    asked = load_extraction_fields("default", "2.0.0", "event.json", True, [])
    assert result["_origins"]["cclom:title"] == "ai"
    assert {result["_origins"][field["id"]] for field in asked} == {"ai"}
    # A field the model is never asked for is the editor's to fill.
    never_asked = set(result["_origins"]) - {field["id"] for field in asked}
    assert never_asked
    assert {result["_origins"][field_id] for field_id in never_asked} == {"user"}


async def test_values_of_the_caller_keep_their_origin_in_a_second_run(monkeypatch):
    result = await _generate(
        monkeypatch,
        {"cclom:title": "Neu"},
        existing_metadata={"cclom:title": "Alt", "cclom:general_description": "Mein"},
        origins={"cclom:general_description": "user"},
    )

    assert result["_origins"]["cclom:title"] == "ai"
    assert result["_origins"]["cclom:general_description"] == "user"
    # Asked for, nothing found, nothing there: not the editor's.
    assert result["_origins"]["cclom:general_keyword"] == "ai"
