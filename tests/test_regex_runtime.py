"""
No input may hold the event loop for more than a moment in a regular expression.

Audit 13.11.2026, P-08 and P-09: two patterns tried every way of splitting a
run of blanks between three quantifiers - cubic in its length. Measured before
the fix: "[IST-STAND]" + 4,000 blanks + "x" took 43 s in /generate, and
"1" + 2,000 blanks + "x" took 102 s in /validate. A re call does not release
the GIL, so the whole pod stood still, probes and /metrics included.

The inputs here are as long as the service accepts (150,000 characters of
text); the bound is generous, the old patterns would not finish at all.
"""

import time

import pytest

from src.services.llm_values import ValueNormalizerMixin
from src.services.metadata_generation import parse_update_text
from src.services.output_normalizer import OutputNormalizer
from src.utils import dates_numbers

LONG = 150_000
BOUND_SECONDS = 2.0


def _timed(function, *args):
    start = time.perf_counter()
    result = function(*args)
    return result, time.perf_counter() - start


# ------------------------------------------------------------ update markers


@pytest.mark.parametrize("marker", ["[IST-STAND]", "[CURRENT]", "[EXISTING]"])
def test_a_marker_without_its_counterpart_costs_no_time(marker):
    text = marker + " " * LONG + "x"

    result, seconds = _timed(parse_update_text, text)

    assert result == (text, None)
    assert seconds < BOUND_SECONDS


@pytest.mark.parametrize(
    "text",
    [
        '[IST-STAND] {"cclom:title": ["Alt"]} [UPDATE] Neuer Text',
        '[ist-stand]{"cclom:title": ["Alt"]}[update]Neuer Text',
        '  [CURRENT]\n{"cclom:title": ["Alt"]}\n[UPDATE]\nNeuer Text  ',
        '[EXISTING] {"cclom:title": ["Alt"]} [NEW] Neuer Text',
    ],
)
def test_the_markers_still_split_text_and_metadata(text):
    assert parse_update_text(text) == ("Neuer Text", {"cclom:title": ["Alt"]})


def test_the_first_update_marker_ends_the_metadata():
    text = '[IST-STAND] {"a": 1} [UPDATE] Text mit [UPDATE] im Satz'

    assert parse_update_text(text) == ("Text mit [UPDATE] im Satz", {"a": 1})


@pytest.mark.parametrize(
    "text",
    [
        "Ein Text ohne Marker",
        "[IST-STAND] kein JSON [UPDATE] Text",
        '[IST-STAND] ["eine", "Liste"] [UPDATE] Text',
        'Vorweg [IST-STAND] {"a": 1} [UPDATE] Text',
    ],
)
def test_anything_else_stays_plain_text(text):
    assert parse_update_text(text) == (text, None)


def test_the_header_of_an_exported_record_is_not_metadata():
    text = '[IST-STAND] {"contextName": "default", "cclom:title": ["Alt"]} [UPDATE] T'

    assert parse_update_text(text) == ("T", {"cclom:title": ["Alt"]})


# ------------------------------------------------------------ dates and numbers


# By name: the values themselves are too long for a test id.
LONG_VALUES = {
    "blanks, then a letter": "1" + " " * LONG + "x",
    "blanks, then a digit": "1" + " " * LONG + "9",
    "commas": "15.03.2025" + "," * LONG,
    "um um um": "15. März 2025 " + "um " * (LONG // 3),
    "digits": "1" * LONG,
    "digits and dots": "1." * (LONG // 2),
}
READERS = {
    "read_datetime": dates_numbers.read_datetime,
    "read_date": dates_numbers.read_date,
    "read_time": dates_numbers.read_time,
    "parse_german_number": dates_numbers.parse_german_number,
    "to_number": lambda value: dates_numbers.to_number(value, integer=False),
}


@pytest.mark.parametrize("value", sorted(LONG_VALUES))
@pytest.mark.parametrize("reader", sorted(READERS))
def test_no_reader_of_dates_or_numbers_stalls_on_a_long_value(reader, value):
    def call():
        try:
            return READERS[reader](LONG_VALUES[value])
        except dates_numbers.ImpossibleDate:
            return None

    _, seconds = _timed(call)

    assert seconds < BOUND_SECONDS


@pytest.mark.parametrize(
    "value, expected",
    [
        ("15. März 2025 14:00", "2025-03-15T14:00"),
        ("15.03.2025, 14:00", "2025-03-15T14:00"),
        ("15.03.2025 um 14 Uhr", "2025-03-15T14:00"),
        ("15.03.2025, um 14:30 Uhr", "2025-03-15T14:30"),
        ("15.03.2025", "2025-03-15T00:00"),
    ],
)
def test_a_time_after_the_date_is_still_read(value, expected):
    assert dates_numbers.read_datetime(value) == expected


def test_a_value_too_long_for_a_date_is_none():
    assert dates_numbers.read_datetime("15.03.2025 " + " " * 200 + "14:00") is None


# ------------------------------------------------------------ LLM answers


def _parse(content: str):
    return ValueNormalizerMixin._parse_json_response(object(), content)


def test_an_unclosed_code_fence_costs_no_time():
    result, seconds = _timed(_parse, "```json" + " " * 20_000 + "x")

    assert seconds < BOUND_SECONDS
    assert result is None


def test_a_fenced_answer_is_still_read():
    assert _parse('```json\n  {"value": "Titel"}\n  ```') == {"value": "Titel"}


# ------------------------------------------------------------ audit 14.11.
# L-30: the dayOfWeek range pattern was quadratic in a run of dashes - 4.9 s
# for 16,000 characters, in /generate also for a caller's existing_metadata.
# L-42: so was the search for {...} in a model answer (287 ms for 8,000 "{").

FAST_SECONDS = 0.05


def _best_of_three(function, *args):
    """The fastest of three runs: a busy machine must not fail a 50 ms bound."""
    result, seconds = _timed(function, *args)
    for _ in range(2):
        seconds = min(seconds, _timed(function, *args)[1])
    return result, seconds


@pytest.mark.parametrize(
    "day",
    [
        "-" * 100_000 + " a b",
        "bis" * 30_000 + " a b",
        "x" * 100_000 + " y z",
        "Mo" + " " * 100_000 + "x",
        "Mo," * 30_000,
    ],
    ids=["dashes", "bis", "no separator", "blanks", "commas"],
)
def test_a_long_day_of_week_costs_no_time_and_stays_as_written(day):
    result, seconds = _best_of_three(OutputNormalizer()._get_day_codes, day)

    assert seconds < FAST_SECONDS
    assert result == [day]


def test_opening_hours_with_a_long_day_of_week_cost_no_time():
    day = "-" * 100_000 + " a b"
    metadata = {"schema:openingHoursSpecification": [{"dayOfWeek": day}]}

    result, seconds = _best_of_three(OutputNormalizer().normalize_output, metadata)

    assert seconds < FAST_SECONDS
    assert result["schema:openingHoursSpecification"] == [{"dayOfWeek": [day]}]


@pytest.mark.parametrize("opening", ["{", "[", '{"value": ', "[["])
def test_an_answer_of_nothing_but_opening_brackets_costs_no_time(opening):
    result, seconds = _best_of_three(_parse, opening * 50_000)

    assert seconds < FAST_SECONDS
    assert result is None


@pytest.mark.parametrize(
    "content, expected",
    [
        ('Die Antwort: {"value": "Titel"} - fertig.', {"value": "Titel"}),
        ('Antwort {"value": {"a": 1}} und {"b": 2}', None),  # two objects: no JSON
        ('Werte: ["a", "b"] gefunden', {"value": ["a", "b"]}),
        ('{"value": ["a", "b"]} Ende', {"value": ["a", "b"]}),
        ("} verdreht {", None),
        ("] verdreht [", None),
    ],
)
def test_json_inside_a_sentence_is_read_as_before(content, expected):
    assert _parse(content) == expected
