"""
Numbers and dates of the final pre-release audit (2026-10-18).

- German thousands separators: "1.234,5" and "12.345.678" were no number;
  integer fields truncated 2.7 to 2. A single dot group ("1.500", "0.950")
  stays a decimal as in 2.1.1 (review 18.10.: "0.950" had become 950).
- An ISO date or date-time was returned unchecked ("2026-02-30",
  "2026-03-01T25:00"), while the German spelling of the same day was refused;
  /validate only checked day <= 31.
- German date-times with a comma or a month name ("01.05.2026, 18:00",
  "15. März 2025 14:00") were not read at all.
"""

import pytest

from src.services.field_normalizer import FieldNormalizer
from src.services.llm_dates_numbers import DateNumberMixin
from src.services.metadata_validation import _date_problem, _datetime_problem
from src.utils.dates_numbers import ImpossibleDate, read_date, read_datetime, to_number


@pytest.mark.parametrize(
    "text, integer, expected",
    [
        ("1.234,5", False, 1234.5),
        ("1.500,0", False, 1500.0),
        ("12.345.678", True, 12345678),
        ("-1.234.567", True, -1234567),
        ("1.5", False, 1.5),  # one decimal digit is no thousands group
        ("12,5", False, 12.5),
        ("2.7", True, 3),
        ("2,4", True, 2),
    ],
)
def test_numbers_as_written_in_german(text, integer, expected):
    assert to_number(text, integer=integer) == expected


@pytest.mark.parametrize(
    "text, integer, expected",
    [
        # A first group that starts with 0 groups no thousands.
        ("0.950", False, 0.95),
        ("-0.125", False, -0.125),
        ("0.500", False, 0.5),
        # One dot group without a comma is ambiguous: read as 2.1.1 did, as an
        # English decimal - what a model writes far more often.
        ("52.520", False, 52.52),
        ("3.141", False, 3.141),
        ("1.500", False, 1.5),
        ("-1.500", False, -1.5),
        # On an integer field the group is thousands since the audit of 14.11.
        # (test_dates_numbers_small_items.py): "1.500" was 2 there.
        ("1.500", True, 1500),
    ],
)
def test_a_single_dot_group_is_a_decimal_as_in_2_1_1(text, integer, expected):
    assert to_number(text, integer=integer) == expected


def test_an_integer_field_rounds_a_float():
    assert to_number(2.7, integer=True) == 3
    assert to_number(2.5, integer=True) == 3


@pytest.mark.parametrize("text", ["2026-02-30", "2025-13-01", "2026-02-30T10:00"])
def test_an_impossible_iso_date_is_refused_like_the_german_one(text):
    with pytest.raises(ImpossibleDate):
        read_date(text)


def test_the_llm_path_drops_an_impossible_iso_date_like_a_german_one():
    assert DateNumberMixin()._normalize_date("2026-02-30") is None


def test_field_normalizer_keeps_an_impossible_iso_date_as_written():
    assert FieldNormalizer()._normalize_date("2026-02-30") == "2026-02-30"


def test_a_leap_day_and_an_old_iso_date_stay_valid():
    assert read_date("2024-02-29") == "2024-02-29"
    assert read_date("1789-07-14") == "1789-07-14"


@pytest.mark.parametrize("text", ["2026-02-30", "2025-04-31", "2025-02-29"])
def test_validation_reports_an_impossible_iso_date(text):
    assert _date_problem(text) == f"Invalid date values in '{text}'"


def test_validation_accepts_a_real_iso_date():
    assert _date_problem("2024-02-29") is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("15. März 2025 14:00", "2025-03-15T14:00"),
        ("01.05.2026, 18:00", "2026-05-01T18:00"),
        ("01.05.2026, 18 Uhr", "2026-05-01T18:00"),
        ("3. Mai 2026 um 9:30 Uhr", "2026-05-03T09:30"),
        ("03.05.2026 14:30", "2026-05-03T14:30"),
        ("2026-05-03 14:30", "2026-05-03T14:30"),
        ("15. März 2025", "2025-03-15T00:00"),
        ("03.05.2026", "2026-05-03T00:00"),
    ],
)
def test_german_date_times(text, expected):
    assert read_datetime(text) == expected


@pytest.mark.parametrize("text", ["30.02.2026, 18:00", "irgendwann 18:00", "Mai 2026"])
def test_unreadable_or_impossible_date_times_give_none(text):
    assert read_datetime(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "2026-02-30T10:00",
        "2025-13-01T10:00",
        "2026-03-01T25:00",
        "2026-03-01T24:00",
        "2026-03-01T10:60",
        "2026-03-01T10:00:61",
        "2026-02-30T10:00:00Z",
    ],
)
def test_an_impossible_iso_date_time_gives_none_like_a_german_one(text):
    assert read_datetime(text) is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2024-02-29T23:59", "2024-02-29T23:59"),
        ("2026-03-01T00:00:59.123+02:00", "2026-03-01T00:00+02:00"),
        ("2026-03-01T10:00Z", "2026-03-01T10:00Z"),
        ("2026-03-01T10:00:00-0130", "2026-03-01T10:00-0130"),
        ("1789-07-14T12:00", "1789-07-14T12:00"),
    ],
)
def test_a_real_iso_date_time_keeps_offset_and_drops_seconds(text, expected):
    assert read_datetime(text) == expected


def test_an_impossible_iso_date_time_stays_as_written_in_the_normalizers():
    assert FieldNormalizer()._normalize_datetime("2026-02-30T10:00") == (
        "2026-02-30T10:00"
    )
    assert DateNumberMixin()._normalize_datetime("2026-03-01T25:00") == (
        "2026-03-01T25:00"
    )


@pytest.mark.parametrize("text", ["2026-02-30T10:00", "2026-03-01T25:00:00"])
def test_validation_reports_an_impossible_iso_date_time(text):
    assert _datetime_problem(text) == f"Invalid datetime values in '{text}'"


def test_validation_accepts_a_real_iso_date_time():
    assert _datetime_problem("2024-02-29T23:59:00+01:00") is None


# ------------------------------------------------- audit 13.11.2026, L-20


@pytest.mark.parametrize(
    "written", ["28.08.1749", "28. August 1749", "28/08/1749", "August 28, 1749"]
)
def test_a_date_before_1900_written_out_in_full_is_a_date(written):
    """
    Goethe's birthday in German spelling was "impossible" and dropped, the
    same day as 1749-08-28 was kept: person.json has schema:birthDate.
    """
    assert read_date(written) == "1749-08-28"


def test_a_day_that_does_not_exist_stays_impossible_in_any_century():
    with pytest.raises(ImpossibleDate):
        read_date("30.02.1749")


def test_a_two_digit_year_is_still_read_into_1950_to_2049():
    assert read_date("28.08.49") == "2049-08-28"
    assert read_date("28.08.50") == "1950-08-28"
