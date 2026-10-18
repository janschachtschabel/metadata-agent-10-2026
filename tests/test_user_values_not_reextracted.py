"""
Fields the editor filled (origin "user") are not sent to the LLM again (#17).

merge_extracted keeps a user value anyway, so asking for it cost one LLM call
per field, the answer was thrown away, and run_outcome counted it as
extracted. Named in regenerate_fields, the editor asks for it explicitly -
and then gets the new value (origin "ai"), not the old one back.
"""

from types import SimpleNamespace

from src.services.field_normalizer import get_field_normalizer
from src.services.metadata_generation import select_fields_to_extract
from src.services.metadata_service import MetadataService
from src.services.output_normalizer import get_output_normalizer

FIELDS = [{"id": "cclom:title"}, {"id": "cclom:general_description"}, {"id": "x"}]
EXISTING = {"cclom:title": "Mein Titel", "cclom:general_description": "Alt"}


def _ids(fields):
    return [f["id"] for f in fields]


def test_a_filled_user_field_is_not_asked_for():
    selected = select_fields_to_extract(
        FIELDS, EXISTING, None, False, origins={"cclom:title": "user"}
    )

    assert _ids(selected) == ["cclom:general_description", "x"]


def test_an_empty_user_field_is_still_asked_for():
    selected = select_fields_to_extract(
        FIELDS, {**EXISTING, "cclom:title": ""}, None, False, {"cclom:title": "user"}
    )

    assert "cclom:title" in _ids(selected)


def test_a_user_field_named_in_regenerate_fields_is_asked_for():
    selected = select_fields_to_extract(
        FIELDS, EXISTING, ["cclom:title"], False, origins={"cclom:title": "user"}
    )

    assert _ids(selected) == ["cclom:title", "x"]


def test_regenerate_empty_skips_a_filled_user_field():
    selected = select_fields_to_extract(
        FIELDS, EXISTING, None, True, origins={"cclom:title": "user"}
    )

    assert _ids(selected) == ["x"]


def test_without_origins_everything_is_asked_for():
    assert _ids(select_fields_to_extract(FIELDS, EXISTING, None, False)) == _ids(FIELDS)


# ------------------------------------------------ end to end (generate_metadata)


class _LLM:
    """Stands in for LLMService: answers fixed values, records what it was asked."""

    provider, model, requires_custom_header = "b-api-openai", "test-model", False

    def __init__(self, values):
        self.values, self.asked = values, []

    async def extract_fields_parallel(
        self, fields, text, existing_metadata, language, max_workers
    ):
        self.asked.extend(f["id"] for f in fields)
        return {
            "values": {f["id"]: self.values.get(f["id"]) for f in fields},
            "errors": [],
            "skipped": 0,
        }


async def _generate(llm, regenerate_fields):
    service = MetadataService.__new__(MetadataService)
    service.llm_service = llm
    service.field_normalizer = get_field_normalizer()
    service.output_normalizer = get_output_normalizer()
    service.geocoding_service = SimpleNamespace()
    service.settings = SimpleNamespace()
    return await service.generate_metadata(
        text="Korrektur.",
        schema_file="event.json",
        existing_metadata={"cclom:title": "Mein Titel"},
        enable_geocoding=False,
        regenerate_fields=regenerate_fields,
        origins={"cclom:title": "user"},
    )


async def test_a_listed_user_field_gets_the_new_value():
    llm = _LLM({"cclom:title": "KI-Titel"})

    result = await _generate(llm, ["cclom:title"])

    assert "cclom:title" in llm.asked
    assert result["metadata"]["cclom:title"] == "KI-Titel"
    assert result["_origins"]["cclom:title"] == "ai"
    assert result["processing"]["fields_extracted"] == 1


async def test_an_unlisted_user_field_keeps_its_value_and_is_not_asked():
    llm = _LLM({"cclom:title": "KI-Titel", "cclom:general_description": "Neu"})

    result = await _generate(llm, ["cclom:general_description"])

    assert "cclom:title" not in llm.asked
    assert result["metadata"]["cclom:title"] == "Mein Titel"
    assert result["_origins"]["cclom:title"] == "user"
    assert result["processing"]["fields_extracted"] == 1
