"""
The shape of a value follows the schema, and nothing is dropped without a word (audit 14.11.2026, L-45).

FieldNormalizer changed the shape of caller values silently: a single-value
field holding ["a", "b"] became "a", and a list field holding a scalar kept
the scalar - the web component and the repository then got a string where the
schema says list. Decided:

- a scalar on a list field becomes a list of one entry (the smaller evil: no
  value is lost, and the shape is the declared one);
- a list on a single-value field still gives its first entry - a field that
  takes one value cannot keep two - and the entries left out are named in a
  note that MetadataService puts into processing.warnings.
"""

import pytest

from src.services.field_normalizer import FieldNormalizer
from src.services.metadata_service import MetadataService

LIST = {"datatype": "array", "multiple": True}
SINGLE = {"datatype": "string"}
PHYSICS = "http://w3id.org/openeduhub/vocabs/discipline/460"
SUBJECTS = {
    "datatype": "array",
    "multiple": True,
    "vocabulary": {
        "type": "skos",
        "concepts": [{"uri": PHYSICS, "label": {"de": "Physik"}}],
    },
}


def _normalize(value, schema, exact_only=True):
    notes: list[str] = []
    result = FieldNormalizer().normalize_field_value(
        value, schema, True, exact_only=exact_only, notes=notes
    )
    return result, notes


# ------------------------------------------------------------ scalar on a list field


@pytest.mark.parametrize("exact_only", [True, False])
@pytest.mark.parametrize(
    "value, schema, expected",
    [
        ("KI", LIST, ["KI"]),
        (7, {"datatype": "array", "multiple": True}, [7]),
        ("3", {"datatype": "integer", "multiple": True}, [3]),
        ("Physik", SUBJECTS, [PHYSICS]),
        (PHYSICS, SUBJECTS, [PHYSICS]),
        # datatype array is a list whatever `multiple` says
        ("KI", {"datatype": "array", "multiple": False}, ["KI"]),
        (0, LIST, [0]),
        (False, LIST, [False]),
    ],
)
def test_a_scalar_on_a_list_field_becomes_a_list_of_one(
    value, schema, expected, exact_only
):
    assert _normalize(value, schema, exact_only) == (expected, [])


def test_a_model_answer_that_is_no_concept_leaves_an_empty_list():
    assert _normalize("Quatschwert", SUBJECTS, exact_only=False) == ([], [])


def test_a_caller_value_that_is_no_concept_is_kept_in_the_list():
    assert _normalize("Quatschwert", SUBJECTS) == (["Quatschwert"], [])


@pytest.mark.parametrize("value", [None, ""])
def test_an_empty_value_on_a_list_field_stays_empty(value):
    assert _normalize(value, LIST) == (value, [])


def test_a_list_on_a_list_field_is_unchanged():
    assert _normalize(["a", "b"], LIST) == (["a", "b"], [])


# ------------------------------------------------------------ list on a single field


def test_a_list_on_a_single_field_names_what_it_left_out():
    value, notes = _normalize(["a", "b", "c"], SINGLE)

    assert value == "a"
    assert notes == ["Feld nimmt einen Wert auf: 2 weitere Wert(e) nicht übernommen."]


@pytest.mark.parametrize("value, expected", [(["a"], "a"), ([], None), ([7], 7)])
def test_a_list_of_one_or_none_drops_nothing(value, expected):
    assert _normalize(value, SINGLE) == (expected, [])


def test_without_a_place_for_notes_the_value_is_the_same():
    normalizer = FieldNormalizer()

    assert normalizer.normalize_field_value(["a", "b"], SINGLE) == "a"
    assert normalizer.normalize_field_value("KI", LIST) == ["KI"]


# ------------------------------------------------------------ processing.warnings


async def _generate(monkeypatch, existing, extracted=None):
    service = MetadataService()

    async def extract_fields_parallel(**kwargs):
        return {"values": extracted or {}, "errors": [], "skipped": 0}

    monkeypatch.setattr(
        service.llm_service, "extract_fields_parallel", extract_fields_parallel
    )
    return await service.generate_metadata(
        text="Ein Text über Physik.",
        schema_file="event.json",
        version="2.0.0",
        existing_metadata=existing,
        enable_geocoding=False,
    )


async def test_dropped_entries_of_a_caller_value_reach_the_warnings(monkeypatch):
    result = await _generate(monkeypatch, {"cclom:title": ["Erster", "Zweiter"]})

    assert result["metadata"]["cclom:title"] == "Erster"
    assert (
        "cclom:title: Feld nimmt einen Wert auf: 1 weitere Wert(e) nicht übernommen."
        in result["processing"]["warnings"]
    )


async def test_dropped_entries_of_a_model_answer_reach_the_warnings(monkeypatch):
    result = await _generate(monkeypatch, {}, {"cclom:title": ["Erster", "Zweiter"]})

    assert result["metadata"]["cclom:title"] == "Erster"
    assert (
        "cclom:title: Feld nimmt einen Wert auf: 1 weitere Wert(e) nicht übernommen."
        in result["processing"]["warnings"]
    )


async def test_a_scalar_from_the_caller_comes_back_as_the_list_the_schema_declares(
    monkeypatch,
):
    result = await _generate(monkeypatch, {"ccm:taxonid": "Physik"})

    assert result["metadata"]["ccm:taxonid"] == [PHYSICS]
    assert result["processing"]["warnings"] == []
