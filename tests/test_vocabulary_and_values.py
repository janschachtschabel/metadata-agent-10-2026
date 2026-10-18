"""
Values the agent fits to a field (audit 2026-10-18, L-07 to L-14).

- A closed vocabulary means "one of these concepts": an answer that matches
  none of them is not a value, neither on the LLM path nor in FieldNormalizer.
- SKOS vocabularies (Fach, Bildungsstufe, Ressourcentyp) are closed lists of
  URIs too; they were treated as open in two of three places.
- "NaN", "inf" and the word "null" are no numbers; "hundertjährig" is no 100.
- Times have one format whatever path normalised them.
"""

import json

import httpx
import pytest

from src.config import Settings
from src.services import llm_service as llm_module
from src.services.field_normalizer import FieldNormalizer
from src.services.llm_dates_numbers import DateNumberMixin
from src.services.llm_service import LLMService, reset_provider_gates
from src.services.metadata_export import format_value
from src.services.metadata_validation import _vocabulary_warnings
from src.utils.schema_loader import get_ai_fillable_fields, get_latest_version

PHYSIK = "http://w3id.org/openeduhub/vocabs/discipline/460"


class _Answers:
    """A gateway that answers each call with the next prepared JSON."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def handler(self, request):
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(answer)},
                        "finish_reason": "stop",
                    }
                ]
            },
        )


@pytest.fixture(autouse=True)
def _fresh_gates():
    reset_provider_gates()
    yield
    reset_provider_gates()


def _service(gateway, monkeypatch, **settings):
    cfg = Settings(
        _env_file=None,
        **{
            "b_api_key": "own-key",
            "llm_retry_delay": 0,
            "llm_max_retries": 2,
            **settings,
        },
    )
    monkeypatch.setattr(llm_module, "get_settings", lambda: cfg)
    service = LLMService(llm_provider="b-api-openai")
    service.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway.handler)
    )
    return service


def _core_field(field_id):
    fields = get_ai_fillable_fields(
        "default", get_latest_version("default"), "core.json"
    )
    return next(f for f in fields if f["id"] == field_id)


# ------------------------------------------------- L-07 / L-09: LLM path


@pytest.mark.asyncio
async def test_an_answer_matching_no_concept_is_asked_again(monkeypatch):
    # ccm:taxonid is a SKOS vocabulary; nothing in ["Foo", "Bar"] is a subject.
    gateway = _Answers({"value": ["Foo", "Bar"]}, {"value": ["Physik"]})
    service = _service(gateway, monkeypatch)

    _, value, error = await service.extract_field(
        _core_field("ccm:taxonid"), "Text über Physik", None, "de"
    )

    assert error is None
    assert value == [PHYSIK]
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_a_partial_match_keeps_its_matches_without_asking_again(monkeypatch):
    # Final audit 18.10.: asking again for a partly matching list threw the
    # matches away whenever a later attempt matched less ([a, b, x] then [x]x3
    # ended as []) and cost up to llm_max_retries more full-text calls. 2.1.1
    # did not ask again either. Unmatched entries are dropped.
    gateway = _Answers({"value": ["Physik", "Quatschwert"]}, {"value": ["Quatsch"]})
    service = _service(gateway, monkeypatch)

    _, value, _ = await service.extract_field(
        _core_field("ccm:taxonid"), "Text", None, "de"
    )

    assert value == [PHYSIK]
    assert gateway.calls == 1


@pytest.mark.asyncio
async def test_when_no_attempt_matches_the_result_is_empty(monkeypatch):
    gateway = _Answers({"value": ["Quatschwert"]})
    service = _service(gateway, monkeypatch)

    _, value, _ = await service.extract_field(
        _core_field("ccm:taxonid"), "Text", None, "de"
    )

    assert value == []
    assert gateway.calls == 3  # first call + llm_max_retries


@pytest.mark.asyncio
async def test_an_empty_list_is_no_reason_to_ask_again(monkeypatch):
    gateway = _Answers({"value": []})
    service = _service(gateway, monkeypatch)

    _, value, _ = await service.extract_field(
        _core_field("ccm:taxonid"), "Text", None, "de"
    )

    assert value == []
    assert gateway.calls == 1


# ---------------------------------------------- L-08 / L-09: FieldNormalizer

CLOSED = {
    "type": "closed",
    "concepts": [
        {"uri": "http://x/a", "label": {"de": "Mathematik"}},
        {"uri": "http://x/b", "label": {"de": "Physik"}},
    ],
}
SKOS = {**CLOSED, "type": "skos"}


@pytest.mark.parametrize("vocabulary", [CLOSED, SKOS])
def test_field_normalizer_drops_values_outside_a_closed_vocabulary(vocabulary):
    normalizer = FieldNormalizer()
    schema = {"datatype": "array", "multiple": True, "vocabulary": vocabulary}

    assert normalizer.normalize_field_value(["Quatschwert", "Physik"], schema) == [
        "http://x/b"
    ]
    assert (
        normalizer.normalize_field_value(
            "Quatschwert", {"datatype": "string", "vocabulary": vocabulary}
        )
        is None
    )


def test_field_normalizer_keeps_free_text_in_an_open_vocabulary():
    normalizer = FieldNormalizer()
    schema = {"datatype": "string", "vocabulary": {**CLOSED, "type": "open"}}

    assert normalizer.normalize_field_value("Eigenes Stichwort", schema) == (
        "Eigenes Stichwort"
    )


def test_validation_warns_about_values_outside_a_skos_vocabulary():
    warnings = _vocabulary_warnings(
        "ccm:taxonid", {"vocabulary": SKOS}, ["http://x/b", "Quatschwert"]
    )

    assert [w["message"] for w in warnings] == [
        "Value 'Quatschwert' not in closed vocabulary"
    ]


# --------------------------------------------------------- L-11: numbers

convert = DateNumberMixin()


@pytest.mark.parametrize("value", ["NaN", "nan", "inf", "-Infinity", "null", "None"])
@pytest.mark.parametrize("datatype", ["number", "integer"])
def test_no_number_is_no_number(value, datatype):
    assert convert._normalize_number(value, datatype) is None
    assert FieldNormalizer()._normalize_number(value, datatype == "integer") is None


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_a_non_finite_float_from_json_is_no_number(value):
    assert convert._normalize_number(value, "number") is None


@pytest.mark.parametrize(
    "value",
    [
        "hundertjährig",
        "tausendfach",
        "einsam",
        "zweiundzwanzigste",
        "dreiundachtzigmal",
    ],
)
def test_words_that_only_contain_a_number_word_are_no_number(value):
    assert convert._normalize_number(value, "integer") is None


@pytest.mark.parametrize(
    "value, expected",
    [("dreihundertvierundsiebzig", 374), ("zweihunderttausend", 200000), ("elf", 11)],
)
def test_field_normalizer_reads_the_same_number_words(value, expected):
    assert FieldNormalizer()._normalize_number(value, True) == expected
    assert convert._normalize_number(value, "integer") == expected


# ------------------------------------------------------ L-10: one format


@pytest.mark.parametrize(
    "value, expected",
    [("14:30:00", "14:30"), ("14:30", "14:30"), ("9:05", "09:05"), ("14 Uhr", "14:00")],
)
def test_times_have_one_format_on_both_paths(value, expected):
    assert convert._normalize_time(value) == expected
    assert FieldNormalizer()._normalize_time(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-05-03T10:00:00", "2026-05-03T10:00"),
        ("03.05.2026 14:30", "2026-05-03T14:30"),
        ("2026-05-03", "2026-05-03T00:00"),
    ],
)
def test_datetimes_have_one_format_on_both_paths(value, expected):
    assert convert._normalize_datetime(value) == expected
    assert FieldNormalizer()._normalize_datetime(value) == expected


def test_field_normalizer_reads_german_month_names():
    assert FieldNormalizer()._normalize_date("3. Mai 2026") == "2026-05-03"


# ------------------------------------------------------ L-12: Markdown export


def test_markdown_export_survives_objects_in_a_vocabulary_list():
    field = {"system": {"vocabulary": CLOSED}}

    text = format_value([{"name": "Ort"}, "http://x/a"], field, "de")

    assert text == "Ort, Mathematik"


def test_markdown_export_labels_concepts_identified_by_value():
    field = {
        "system": {
            "vocabulary": {
                "type": "closed",
                "concepts": [{"value": "de", "label": {"de": "Deutsch"}}],
            }
        }
    }

    assert format_value("de", field, "de") == "Deutsch"


# ------------------------------------------- L-13 / L-14: the field prompt


def test_page_text_with_update_in_it_is_no_correction(monkeypatch):
    service = _service(_Answers({"value": None}), monkeypatch)
    field = _core_field("cclom:title")

    prompt = service._build_extraction_prompt(
        field, "Release notes\n--- UPDATE ---\nVersion 2", "Alter Titel", "de"
    )

    assert "KORREKTUR-MODUS" not in prompt


def test_a_correction_built_by_the_agent_is_recognised(monkeypatch):
    from src.api.input_resolver import with_correction

    from src.services.llm_prompts import correction_round

    service = _service(_Answers({"value": None}), monkeypatch)
    text, _ = with_correction("Seite", {"cclom:title": "Alt"}, "Titel kürzer")

    with correction_round(True):
        prompt = service._build_extraction_prompt(
            _core_field("cclom:title"), text, "Alt", "de"
        )
    # The same text without the mark of /generate: a page quoting the header.
    unmarked = service._build_extraction_prompt(
        _core_field("cclom:title"), text, "Alt", "de"
    )

    assert "KORREKTUR-MODUS" in prompt
    assert "KORREKTUR-MODUS" not in unmarked


def test_only_the_last_retry_says_last_attempt(monkeypatch):
    service = _service(_Answers({"value": None}), monkeypatch, llm_max_retries=3)
    field = _core_field("cclom:title")

    second = service._build_extraction_prompt(field, "T", None, "de", retry_count=2)
    last = service._build_extraction_prompt(
        field, "T", None, "de", retry_count=3, last_attempt=True
    )

    assert "LETZTER VERSUCH" not in second
    assert "LETZTER VERSUCH" in last


# ------------------------------------------------- P-06: no retry for bugs


@pytest.mark.asyncio
async def test_a_programming_error_is_not_sent_to_the_model_again(monkeypatch, caplog):
    gateway = _Answers({"value": "Titel"})
    service = _service(gateway, monkeypatch, llm_max_retries=3)

    def broken(value, field):
        raise KeyError("system")

    monkeypatch.setattr(service, "_normalize_value", broken)

    _, value, error = await service.extract_field(
        _core_field("cclom:title"), "Text", None, "de"
    )

    assert value is None
    assert error == "Internal error while extracting cclom:title"
    assert gateway.calls == 1
    assert "KeyError" in caplog.text


# --------------------------------------------- L-03: nested body, precise LRT


def test_the_extracted_lrt_wins_also_in_the_nested_body_form():
    from src.services.repository_metadata import _extended_fields

    precise = "http://w3id.org/openeduhub/vocabs/new_lrt/arbeitsblatt"
    nested = {
        "metadataset": "learning_material.json",
        "metadata": {"cclom:title": "Brüche", "oeh:new_lrt": [precise]},
    }

    fields = _extended_fields(nested, "default", "latest", None)

    assert "ccm:oeh_lrt" not in fields, "the coarse type must not replace it"


# ---------------------------------------------------- L-15: small remainders


def test_detection_names_the_profile_of_each_content_type():
    from src.utils.schema_loader import get_content_types

    types = {t["schema_file"]: t for t in get_content_types("default", "latest")}

    assert types["event.json"]["profile_id"] == "type:event"


def test_no_request_path_changes_the_cached_schemas(monkeypatch):
    """load_schema hands out the cached dict itself; nobody may write into it."""
    import hashlib

    from src.services.field_normalizer import get_field_normalizer
    from src.services.metadata_export import export_to_markdown
    from src.services.metadata_validation import validate_metadata
    from src.utils.schema_loader import load_schema

    def digest():
        return {
            name: hashlib.sha256(
                json.dumps(
                    load_schema("default", "latest", name), sort_keys=True
                ).encode()
            ).hexdigest()
            for name in ("core.json", "event.json", "learning_material.json")
        }

    before = digest()
    record = {
        "metadataset": "event.json",
        "cclom:title": "Kurs",
        "ccm:taxonid": ["Physik", "Quatsch"],
        "schema:location": [{"name": "Weimar"}],
    }
    validate_metadata(dict(record), get_field_normalizer(), "default", "latest")
    export_to_markdown(dict(record), "default", "latest")
    for schema in ("event.json", "learning_material.json"):
        for field in load_schema("default", "latest", schema)["fields"]:
            get_field_normalizer().normalize_field_value(
                ["Physik", "x"], field.get("system", {})
            )

    assert digest() == before


# ------------------------------------------------ review of 2026-10-18 round


@pytest.mark.asyncio
async def test_a_skos_vocabulary_without_concepts_takes_the_answer_once(monkeypatch):
    # oeh:didactic_method is skos with an empty concept list: nothing to check
    # against, so no retry and no warning (review finding 2).
    gateway = _Answers({"value": ["Gruppenarbeit"]})
    service = _service(gateway, monkeypatch)
    field = {
        "id": "oeh:didactic_method",
        "system": {
            "datatype": "array",
            "multiple": True,
            "vocabulary": {"type": "skos", "concepts": []},
        },
    }

    _, value, _ = await service.extract_field(field, "Text", None, "de")

    assert value == ["Gruppenarbeit"]
    assert gateway.calls == 1
    assert (
        _vocabulary_warnings(
            "oeh:didactic_method",
            {"vocabulary": {"type": "skos", "concepts": []}},
            ["Gruppenarbeit"],
        )
        == []
    )


@pytest.mark.parametrize("value, expected", [(0, "0"), (False, "false"), ("0", "0")])
def test_zero_and_false_stay_concepts_of_a_closed_vocabulary(value, expected):
    # Review finding 6: falsy answers became None.
    vocabulary = {
        "type": "closed",
        "concepts": [
            {"value": "0", "label": {"de": "0"}},
            {"value": "false", "label": {"de": "nein"}},
        ],
    }
    result = FieldNormalizer().normalize_field_value(
        value, {"datatype": "string", "vocabulary": vocabulary}
    )

    assert result is not None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-05-03T14:30:00Z", "2026-05-03T14:30Z"),
        ("2026-05-03T14:30:00+02:00", "2026-05-03T14:30+02:00"),
        ("2026-05-03T14:30:00.123+0200", "2026-05-03T14:30+0200"),
    ],
)
def test_a_datetime_keeps_its_offset(value, expected):
    # Review finding 7: the offset was dropped, shifting UTC times.
    assert convert._normalize_datetime(value) == expected
    assert FieldNormalizer()._normalize_datetime(value) == expected
