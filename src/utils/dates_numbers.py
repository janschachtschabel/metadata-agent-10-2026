"""
Numbers (also German number words), dates, date-times and times as written in
texts and model answers - one implementation for the LLM path
(llm_dates_numbers), FieldNormalizer and the validation hints.

Formats: dates YYYY-MM-DD, date-times YYYY-MM-DDTHH:MM, times HH:MM - what the
widget writes and what /generate returned by default before (audit L-10).
A string that cannot be read is returned as written: an editor sees and fixes
it, nothing is lost. Non-strings give None.
"""

import math
import re
from datetime import date
from typing import Any, Optional

GERMAN_MONTHS = {
    "januar": 1,
    "jan": 1,
    "februar": 2,
    "feb": 2,
    "märz": 3,
    "mär": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "dez": 12,
}

_ONES = {
    "ein": 1,
    "eins": 1,
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
}
_TEENS = {
    "zehn": 10,
    "elf": 11,
    "zwölf": 12,
    "dreizehn": 13,
    "vierzehn": 14,
    "fünfzehn": 15,
    "sechzehn": 16,
    "siebzehn": 17,
    "achtzehn": 18,
    "neunzehn": 19,
}
_TENS = {
    "zwanzig": 20,
    "dreißig": 30,
    "dreissig": 30,
    "vierzig": 40,
    "fünfzig": 50,
    "sechzig": 60,
    "siebzig": 70,
    "achtzig": 80,
    "neunzig": 90,
}

# What a model writes for "no value". The German word for zero is among them on
# purpose: a model writes 0 for zero, "null" means JSON null in quotes.
_NO_NUMBER = {"null", "none", "nan", "inf", "-inf", "+inf", "infinity", "-infinity"}


def _under_100(text: str) -> Optional[int]:
    simple = _ONES.get(text) or _TEENS.get(text) or _TENS.get(text)
    if simple:
        return simple
    ones, sep, tens = text.partition("und")
    if sep and ones in _ONES and tens in _TENS:
        return _ONES[ones] + _TENS[tens]
    return None


def _under_1000(text: str) -> Optional[int]:
    head, sep, rest = text.partition("hundert")
    if not sep:
        return _under_100(text)
    hundreds = 1 if head in ("", "ein", "eins") else _ONES.get(head)
    if not hundreds:
        return None
    if not rest:
        return hundreds * 100
    tail = _under_100(rest)
    return hundreds * 100 + tail if tail else None


def parse_german_number(text: str) -> Optional[int]:
    """
    'dreihundertvierundsiebzig' -> 374, 'zweitausendfünfhundert' -> 2500.

    The whole word must be a number: 'hundertjährig' or 'tausendfach' are none.
    """
    text = text.strip().lower()
    # "ein" alone is the article ("ein Workshop"), not the number; inside a
    # number word ("einhundert") it counts (audit 14.11., L-N).
    if not text or text == "ein":
        return None
    head, sep, rest = text.partition("tausend")
    if not sep:
        return _under_1000(text)
    thousands = 1 if head in ("", "ein", "eins") else _under_1000(head)
    if not thousands:
        return None
    if not rest:
        return thousands * 1000
    tail = _under_1000(rest)
    return thousands * 1000 + tail if tail else None


# "12.345.678" or "1.234,5": dots group thousands, a comma marks the decimals.
# A first group starting with 0 groups nothing ("0.950" is 0.95). One dot group
# without a comma ("1.500", "52.520") is ambiguous; it stays a decimal as in
# 2.1.1 - a model writes English decimals far more often than that.
_GERMAN_THOUSANDS = re.compile(
    r"^[+-]?[1-9]\d{0,2}(?:(?:\.\d{3}){2,}(?:,\d+)?|\.\d{3}(?:\.\d{3})*,\d+)$"
)
# The ambiguous one dot group. An integer field has no decimals, so there it
# groups thousands: "1.500" visitors were 2 (audit 14.11., L-N).
_ONE_DOT_GROUP = re.compile(r"^[+-]?[1-9]\d{0,2}\.\d{3}$")


def to_number(value: Any, integer: bool) -> Optional[float | int]:
    """
    A finite number from a number or a numeric string / German number word.

    Integer fields round half up (2.7 people are 3, not 2).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None
        return _rounded(value) if integer else value
    if not isinstance(value, str):
        return None

    text = value.lower().strip()
    if text in _NO_NUMBER:
        return None
    word = parse_german_number(text)
    if word is not None:
        return word
    text = text.replace(" ", "")
    # float() takes Python's "1_000"; nobody writes a number that way.
    if "_" in text:
        return None
    if _GERMAN_THOUSANDS.match(text) or (integer and _ONE_DOT_GROUP.match(text)):
        text = text.replace(".", "")
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return _rounded(number) if integer else number


def _rounded(number: float) -> int:
    return math.floor(number + 0.5)


# (pattern, order of the groups as (day, month, year))
_NUMERIC_DATES = [
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), False),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$"), True),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), False),
    (re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"), False),
]
# "15. März 2025", "1. Sept. 2025" and "März 15, 2025": a month name may have
# an umlaut and, abbreviated, a dot (audit 14.11., L-N).
_DAY_MONTH_NAME = re.compile(
    r"^(\d{1,2})\.?\s*([a-zäöü]+)\.?\s+(\d{4})$", re.IGNORECASE
)
_MONTH_NAME_DAY = re.compile(r"^([a-zäöü]+)\.?\s+(\d{1,2}),?\s*(\d{4})$", re.IGNORECASE)


class ImpossibleDate(ValueError):
    """A date in a known format that does not exist, such as 30.02.2026."""


def read_date(text: str) -> Optional[str]:
    """
    YYYY-MM-DD for a date in a known format, None for an unknown format.

    Raises ImpossibleDate for a known format with an impossible date: the
    callers differ in what they do with it.
    """
    val = text.strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:$|T)", val)
    if match:
        return _checked_iso(*(int(part) for part in match.groups()))

    for pattern, two_digit_year in _NUMERIC_DATES:
        match = pattern.match(val)
        if match:
            year = int(match.group(3))
            if two_digit_year:
                year = 2000 + year if year < 50 else 1900 + year
            return _checked(int(match.group(1)), int(match.group(2)), year)

    match = _DAY_MONTH_NAME.match(val)
    if match and match.group(2).lower() in GERMAN_MONTHS:
        month = GERMAN_MONTHS[match.group(2).lower()]
        return _checked(int(match.group(1)), month, int(match.group(3)))

    match = _MONTH_NAME_DAY.match(val)
    if match and match.group(1).lower() in GERMAN_MONTHS:
        month = GERMAN_MONTHS[match.group(1).lower()]
        return _checked(int(match.group(2)), month, int(match.group(3)))
    return None


def _checked_iso(year: int, month: int, day: int) -> str:
    """An ISO date that exists; any year - it is meant as written."""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        raise ImpossibleDate(f"{year:04d}-{month:02d}-{day:02d}") from None


def _checked(day: int, month: int, year: int) -> str:
    """
    A date in German spelling that exists. Its year is written out in full or
    was read into 1950-2049, so it is meant as it stands: 28.08.1749 is a date
    like 1749-08-28 (audit 13.11., L-20).
    """
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        raise ImpossibleDate(f"{day}.{month}.{year}") from None


def read_time(text: str) -> Optional[str]:
    """HH:MM for '14:30', '14:30:00', '9:05', '14 Uhr', '14 Uhr 30', '14:30 Uhr'."""
    val = text.strip()
    match = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", val) or re.match(
        r"^(\d{1,2})(?::(\d{2}))?\s*[Uu]hr\s*(\d{2})?$", val
    )
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(next((g for g in match.groups()[1:] if g), 0))
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return None


def read_datetime(text: str) -> Optional[str]:
    """
    YYYY-MM-DDTHH:MM for an ISO or German date-time, or a date alone (midnight).

    An ISO offset (Z, +02:00) is kept: without it a time given in UTC would be
    read as local time. A date or time that does not exist gives None.
    """
    val = text.strip()
    if len(val) > MAX_DATETIME_LENGTH:
        return None
    match = _ISO_DATETIME.match(val)
    if match:
        return _checked_iso_datetime(match)
    # An ISO date-time with something else behind it ("…T14:30garbage") is
    # unreadable. Read on, its date alone would make it midnight.
    if _ISO_DATE_AND_T.match(val):
        return None

    try:
        day = read_date(val)
        if day is not None:
            return f"{day}T00:00"
        # A trailing time after a space or comma, optionally "um": the date in
        # front may itself contain spaces ("15. März 2025 14:00").
        match = _TRAILING_TIME.match(val)
        if not match:
            return None
        day = read_date(match.group(1))
    except ImpossibleDate:
        return None
    time = read_time(match.group(2))
    return f"{day}T{time}" if day and time else None


_ISO_DATETIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?"
    r"(Z|[+-]\d{2}:?\d{2})?$"
)
_ISO_DATE_AND_T = re.compile(r"^\d{4}-\d{2}-\d{2}T")


def _checked_iso_datetime(match: re.Match[str]) -> Optional[str]:
    year, month, day, hour, minute = (int(part) for part in match.groups()[:5])
    second = int(match.group(6) or 0)
    if not (hour <= 23 and minute <= 59 and second <= 59):
        return None
    try:
        day_part = _checked_iso(year, month, day)
    except ImpossibleDate:
        return None
    return f"{day_part}T{hour:02d}:{minute:02d}{match.group(7) or ''}"


# No date-time is longer ("Samstag, den 15. März 2025, um 14:30 Uhr" has 41
# characters). _TRAILING_TIME splits a run of blanks between three quantifiers:
# on a value of 2,000 characters that took 102 s (audit 13.11., P-09).
MAX_DATETIME_LENGTH = 100

_TRAILING_TIME = re.compile(
    r"^(.+?)\s*(?:,\s*|\s+)(?:um\s+)?(\d{1,2}(?::\d{2}){0,2}(?:\s*[Uu]hr(?:\s*\d{2})?)?)$"
)
