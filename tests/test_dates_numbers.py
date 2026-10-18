"""
German dates, times and numbers as the LLM writes them (audit T-01).

DateNumberMixin turns "3. Mai 2026" or "dreihundertfünfzig" into what the
repository stores. Until now it was only reached through other tests (32 %
branch coverage); these test it directly, against the values a person reading
the text would expect.
"""

import pytest

from src.services.llm_dates_numbers import DateNumberMixin

convert = DateNumberMixin()


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-05-03", "2026-05-03"),
        ("2026-05-03T10:00:00", "2026-05-03"),
        ("03.05.2026", "2026-05-03"),
        ("3.5.2026", "2026-05-03"),
        ("3. Mai 2026", "2026-05-03"),
        ("3 Mai 2026", "2026-05-03"),
        ("15. März 2025", "2025-03-15"),
        ("1. Dezember 2026", "2026-12-01"),
        ("September 15, 2026", "2026-09-15"),
    ],
)
def test_dates_become_iso(value, expected):
    assert convert._normalize_date(value) == expected


@pytest.mark.parametrize("value", ["Mai 2026", "2026", "irgendwann", ""])
def test_a_date_it_cannot_read_is_kept_as_written(value):
    """Kept rather than dropped: the editor sees and fixes it, nothing is lost."""
    assert convert._normalize_date(value) == value


@pytest.mark.parametrize("value", [None, 20260503, "31.13.2026", "30.02.2026"])
def test_no_string_or_an_impossible_date_gives_none(value):
    assert convert._normalize_date(value) is None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-05-03T10:00:00", "2026-05-03T10:00"),
        ("2026-05-03T10:00", "2026-05-03T10:00"),
        ("03.05.2026 14:30", "2026-05-03T14:30"),
    ],
)
def test_datetimes_become_iso(value, expected):
    """To the minute, as the widget writes them and FieldNormalizer returned (audit L-10)."""
    assert convert._normalize_datetime(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("14:30:00", "14:30"),
        ("14:30", "14:30"),
        ("9:05", "09:05"),
        ("14 Uhr", "14:00"),
        ("14:30 Uhr", "14:30"),
    ],
)
def test_times_become_hh_mm(value, expected):
    """HH:MM, as the widget writes them and FieldNormalizer returned (audit L-10)."""
    assert convert._normalize_time(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("eins", 1),
        ("zwölf", 12),
        ("siebzehn", 17),
        ("zwanzig", 20),
        ("einundzwanzig", 21),
        ("neunundneunzig", 99),
        ("hundert", 100),
        ("dreihundertfünfzig", 350),
        ("tausend", 1000),
        ("zweitausendfünfhundert", 2500),
    ],
)
def test_german_number_words(value, expected):
    assert convert._normalize_number(value, "integer") == expected


@pytest.mark.parametrize(
    "value, datatype, expected",
    [
        (42, "integer", 42),
        (42.7, "integer", 42),
        (42.7, "number", 42.7),
        ("12,5", "number", 12.5),
        ("1 200", "integer", 1200),
    ],
)
def test_numbers_and_numeric_strings(value, datatype, expected):
    assert convert._normalize_number(value, datatype) == expected


@pytest.mark.parametrize("value", [None, "viele", "", [3]])
def test_what_is_no_number_gives_none(value):
    assert convert._normalize_number(value, "number") is None
