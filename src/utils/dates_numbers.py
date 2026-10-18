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
    if not text:
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


def to_number(value: Any, integer: bool) -> Optional[float | int]:
    """A finite number from a number or a numeric string / German number word."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None
        return int(value) if integer else value
    if not isinstance(value, str):
        return None

    text = value.lower().strip()
    if text in _NO_NUMBER:
        return None
    word = parse_german_number(text)
    if word is not None:
        return word
    try:
        number = float(text.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return int(number) if integer else number


def iso_date(year: int, month: int, day: int) -> Optional[str]:
    """YYYY-MM-DD for a date that exists between 1900 and 2100, else None."""
    if not (1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100):
        return None
    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        days_in_month[1] = 29
    if day > days_in_month[month - 1]:
        return None
    return f"{year}-{month:02d}-{day:02d}"


# (pattern, order of the groups as (day, month, year))
_NUMERIC_DATES = [
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), False),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$"), True),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), False),
    (re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"), False),
]
_DAY_MONTH_NAME = re.compile(r"^(\d{1,2})\.?\s*([a-zäöü]+)\s+(\d{4})$", re.IGNORECASE)
_MONTH_NAME_DAY = re.compile(r"^([a-z]+)\s+(\d{1,2}),?\s*(\d{4})$", re.IGNORECASE)


class ImpossibleDate(ValueError):
    """A date in a known format that does not exist, such as 30.02.2026."""


def read_date(text: str) -> Optional[str]:
    """
    YYYY-MM-DD for a date in a known format, None for an unknown format.

    Raises ImpossibleDate for a known format with an impossible date: the
    callers differ in what they do with it.
    """
    val = text.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", val):
        return val
    match = re.match(r"^(\d{4}-\d{2}-\d{2})T", val)
    if match:
        return match.group(1)

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


def _checked(day: int, month: int, year: int) -> str:
    iso = iso_date(year, month, day)
    if iso is None:
        raise ImpossibleDate(f"{day}.{month}.{year}")
    return iso


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
    read as local time.
    """
    val = text.strip()
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?",
        val,
    )
    if match:
        return f"{match.group(1)}T{match.group(2)}{match.group(3) or ''}"

    date_part, _, time_part = val.partition(" ")
    try:
        day = read_date(date_part)
    except ImpossibleDate:
        return None
    if day is None:
        return None
    if not time_part:
        return f"{day}T00:00"
    time = read_time(time_part)
    return f"{day}T{time}" if time else None
