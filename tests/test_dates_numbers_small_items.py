"""
Numbers and dates the readers got wrong or lost (audit 14.11.2026, list L-N).

- to_number("1.500") on an integer field was 2: one dot group is ambiguous for
  a decimal field (it stays 1.5 there, as in 2.1.1), but an integer field has
  no decimals - the dot groups thousands.
- "1_000" was 1000 (Python's own spelling, nobody writes a number that way)
  and "ein" was 1 (the article of "ein Workshop"): neither is a number.
- "März 15, 2025" and "1. Sept. 2025" were not read: no umlaut in the pattern
  for month-first dates, no dot after an abbreviated month.
- "2025-03-15T14:30garbage" was read as 14:30: the ISO pattern had no end.
  Text after an ISO date-time makes it unreadable, not midnight.
- "03/15/2025" (month first, as in the US) is no date in the German reading
  and was dropped from a model answer without a word. It is still not guessed;
  the run says so in processing.warnings. A caller's value stays as written.
"""

import json

import httpx
import pytest

from src.config import Settings
from src.services.field_normalizer import FieldNormalizer
from src.services.llm_service import LLMService, reset_provider_gates
from src.services.metadata_service import MetadataService
from src.utils import dates_numbers
from src.utils.dates_numbers import read_date, read_datetime, to_number
from conftest import use_settings

# ------------------------------------------------------------ numbers


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1.500", 1500),
        ("12.000", 12000),
        ("250.000", 250000),
        ("-1.500", -1500),
        ("1.500,00", 1500),
        ("1.234.567", 1234567),
        # No thousands group: a decimal, rounded half up.
        ("1.5", 2),
        ("1.50", 2),
        ("1.5000", 2),
        ("0.950", 1),
        ("1500", 1500),
        (1.5, 2),
    ],
)
def test_one_dot_group_on_an_integer_field_groups_thousands(value, expected):
    assert to_number(value, integer=True) == expected
    assert FieldNormalizer().normalize_field_value(value, {"datatype": "integer"}) == (
        expected
    )


@pytest.mark.parametrize(
    "value, expected",
    [("1.500", 1.5), ("52.520", 52.52), ("12.000", 12.0), ("1.234.567", 1234567.0)],
)
def test_on_a_decimal_field_one_dot_group_stays_a_decimal(value, expected):
    assert to_number(value, integer=False) == expected


@pytest.mark.parametrize("value", ["1_000", "1_0", "_1", "1__0", "1_000.5", "ein"])
@pytest.mark.parametrize("integer", [True, False])
def test_python_spellings_and_the_article_are_no_numbers(value, integer):
    assert to_number(value, integer=integer) is None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("eins", 1),
        ("einhundert", 100),
        ("einundzwanzig", 21),
        ("eintausend", 1000),
        ("hundert", 100),
        ("1000", 1000),
        ("1 000", 1000),
    ],
)
def test_number_words_with_ein_in_them_are_still_read(value, expected):
    assert to_number(value, integer=True) == expected


# ------------------------------------------------------------ dates


@pytest.mark.parametrize(
    "value, expected",
    [
        ("März 15, 2025", "2025-03-15"),
        ("märz 15 2025", "2025-03-15"),
        ("Januar 15, 2025", "2025-01-15"),
        ("Sept. 1, 2025", "2025-09-01"),
        ("1. Sept. 2025", "2025-09-01"),
        ("1. Sept 2025", "2025-09-01"),
        ("15. Mär. 2025", "2025-03-15"),
        ("3 Dez. 2025", "2025-12-03"),
        ("15. März 2025", "2025-03-15"),
    ],
)
def test_month_names_with_umlauts_and_dots_are_read(value, expected):
    assert read_date(value) == expected
    assert FieldNormalizer().normalize_field_value(value, {"datatype": "date"}) == (
        expected
    )


@pytest.mark.parametrize("value", ["1. Septembär 2025", "Sept.. 1, 2025", "1. . 2025"])
def test_what_is_no_month_is_still_no_date(value):
    assert read_date(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "2025-03-15T14:30garbage",
        "2025-03-15T14:30:00Zx",
        "2025-03-15T14:30 Uhr",
        "2025-03-15T1430",
        "2025-03-15T14",
    ],
)
def test_text_after_an_iso_datetime_makes_it_unreadable(value):
    assert read_datetime(value) is None
    # Unreadable stays as written - it is not turned into another time.
    assert FieldNormalizer().normalize_field_value(value, {"datatype": "datetime"}) == (
        value
    )


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2025-03-15T14:30", "2025-03-15T14:30"),
        ("2025-03-15T14:30:59", "2025-03-15T14:30"),
        ("2025-03-15T14:30:00.123Z", "2025-03-15T14:30Z"),
        ("2025-03-15T14:30+02:00", "2025-03-15T14:30+02:00"),
        ("2025-03-15T14:30:00+0200", "2025-03-15T14:30+0200"),
        ("2025-03-15", "2025-03-15T00:00"),
        ("15.03.2025 14:30", "2025-03-15T14:30"),
    ],
)
def test_complete_datetimes_are_read_as_before(value, expected):
    assert read_datetime(value) == expected


def test_a_date_field_still_takes_the_date_of_an_iso_datetime():
    assert read_date("2025-03-15T14:30") == "2025-03-15"


def test_the_longest_inputs_still_cost_no_time():
    import time

    start = time.perf_counter()
    for value in ("a" * 150_000 + " 1, 2025", "1." + " " * 150_000 + "x 2025"):
        assert dates_numbers.read_date(value) is None
    assert time.perf_counter() - start < 2.0


# ------------------------------------------------------------ 03/15/2025


DATE_FIELD = {
    "id": "schema:startDate",
    "label": {"de": "Beginn"},
    "system": {"datatype": "date"},
}
DATES_FIELD = {
    "id": "schema:dates",
    "label": {"de": "Termine"},
    "system": {"datatype": "date", "multiple": True},
}


@pytest.fixture
def llm(monkeypatch):
    """An LLMService whose gateway answers with the value set on `llm.answer`."""
    reset_provider_gates()
    cfg = Settings(_env_file=None, b_api_key="own-key", llm_retry_delay=0)
    use_settings(monkeypatch, cfg)
    service = LLMService(llm_provider="b-api-openai")

    def handler(request):
        content = json.dumps({"value": service.answer})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    service.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    yield service
    reset_provider_gates()


async def test_a_month_first_date_from_the_model_is_left_out_with_a_warning(llm):
    llm.answer = "03/15/2025"

    extraction = await llm.extract_fields_parallel([DATE_FIELD], "Text")

    assert extraction["values"] == {}
    assert extraction["warnings"] == [
        "schema:startDate: '03/15/2025' ist kein Datum in der Lesart Tag/Monat/Jahr "
        "und wurde nicht übernommen."
    ]


async def test_each_impossible_date_of_a_list_is_named(llm):
    llm.answer = ["15.03.2025", "03/15/2025", "30.02.2026"]

    extraction = await llm.extract_fields_parallel([DATES_FIELD], "Text")

    assert extraction["values"] == {"schema:dates": ["2025-03-15", None, None]}
    assert len(extraction["warnings"]) == 2
    assert "'30.02.2026'" in extraction["warnings"][1]


@pytest.mark.parametrize("answer", ["15.03.2025", "2025-03-15", "irgendwann", None])
async def test_a_readable_or_free_form_date_gives_no_warning(llm, answer):
    llm.answer = answer

    extraction = await llm.extract_fields_parallel([DATE_FIELD], "Text")

    assert extraction["warnings"] == []


async def test_the_warning_reaches_the_processing_block(llm, monkeypatch):
    from src.services import metadata_service as metadata_module

    llm.answer = "03/15/2025"
    monkeypatch.setattr(metadata_module, "get_llm_service", lambda **kw: llm)
    monkeypatch.setattr(
        metadata_module, "load_extraction_fields", lambda *args: [DATE_FIELD]
    )

    result = await MetadataService().generate_metadata(
        "Text", schema_file="event.json", version="2.0.0", enable_geocoding=False
    )

    assert not result["metadata"].get("schema:startDate")
    assert any(
        warning.startswith("schema:startDate: '03/15/2025' ist kein Datum")
        for warning in result["processing"]["warnings"]
    )


def test_a_month_first_date_of_the_caller_stays_as_written():
    normalizer = FieldNormalizer()

    assert normalizer.normalize_field_value("03/15/2025", {"datatype": "date"}) == (
        "03/15/2025"
    )
