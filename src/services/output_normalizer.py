"""
Output normalizer for metadata API.
Transforms API output to match the structure expected by the Canvas web component.
"""

import logging
import re
from datetime import date
from typing import Any, Optional

from ..utils.dates_numbers import ImpossibleDate, read_date, read_datetime

logger = logging.getLogger(__name__)


class OutputNormalizer:
    """Normalizes metadata output to match Canvas web component structure."""

    # Day of week mapping
    DAY_MAPPING = {
        "monday": "MO",
        "montag": "MO",
        "mo": "MO",
        "tuesday": "TU",
        "dienstag": "TU",
        "di": "TU",
        "wednesday": "WE",
        "mittwoch": "WE",
        "mi": "WE",
        "thursday": "TH",
        "donnerstag": "TH",
        "do": "TH",
        "friday": "FR",
        "freitag": "FR",
        "fr": "FR",
        "saturday": "SA",
        "samstag": "SA",
        "sa": "SA",
        "sunday": "SU",
        "sonntag": "SU",
        "so": "SU",
        # Schema.org format
        "schema:monday": "MO",
        "schema:tuesday": "TU",
        "schema:wednesday": "WE",
        "schema:thursday": "TH",
        "schema:friday": "FR",
        "schema:saturday": "SA",
        "schema:sunday": "SU",
    }

    def normalize_output(
        self, metadata: dict[str, Any], warnings: Optional[list[str]] = None
    ) -> dict[str, Any]:
        """
        Normalize all metadata fields to match Canvas web component structure.

        `warnings` receives a line for entries of the opening hours or the
        schedule that are no objects and were left out.
        """
        result = metadata.copy()

        # Note: lifting bare strings into objects is NOT done here any more.
        # This used to be a list of six field names, which covered 4 of the 37
        # object-typed fields the schemas declare — person and organization had
        # no coverage at all, and that is where the staging damage happened.
        # FieldNormalizer._is_object_field now derives it from the schema and
        # runs before this method. What the list produced - the object key
        # convention - is pinned in tests/test_output_normalizer_list_is_redundant.py.

        # Opening hours: normalize dayOfWeek to array with short codes
        if "schema:openingHoursSpecification" in result:
            result["schema:openingHoursSpecification"] = self._normalize_opening_hours(
                result["schema:openingHoursSpecification"], warnings
            )

        # Event schedule: add missing fields
        if "schema:eventSchedule" in result:
            result["schema:eventSchedule"] = self._normalize_event_schedule(
                result["schema:eventSchedule"], warnings
            )

        # Access service: convert strings to objects
        if "schema:accessService" in result:
            result["schema:accessService"] = self._normalize_access_service(
                result["schema:accessService"]
            )

        # About: ensure labels not URIs (keep as-is if already labels)
        if "schema:about" in result:
            result["schema:about"] = self._normalize_about(result["schema:about"])

        # Note: DateTime normalization is handled generically by FieldNormalizer
        # based on the field's datatype. No hardcoded field lists needed here.

        return result

    def _normalize_opening_hours(
        self, hours: Any, warnings: Optional[list[str]] = None
    ) -> list[dict]:
        """Normalize opening hours specification."""
        if not hours or not isinstance(hours, list):
            return hours if hours else []

        _warn_about_non_objects("schema:openingHoursSpecification", hours, warnings)
        result = []
        for item in hours:
            if not isinstance(item, dict):
                continue

            normalized = item.copy()

            # Convert dayOfWeek to array with short codes
            if "dayOfWeek" in normalized:
                dow = normalized["dayOfWeek"]
                days = [dow] if isinstance(dow, str) else dow
                if isinstance(days, list):
                    normalized["dayOfWeek"] = [
                        code for day in days for code in self._get_day_codes(day)
                    ]

            # Remove validThrough if same as validFrom
            if normalized.get("validThrough") == normalized.get("validFrom"):
                normalized.pop("validThrough", None)

            result.append(normalized)

        return result

    WEEK = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
    # "Mo-Fr", "Mo – Fr", "Montag bis Freitag"
    _DAY_RANGE = re.compile(r"^\s*(\S+?)\s*(?:-|–|—|bis)\s*(\S+)\s*$", re.IGNORECASE)

    # A range has under 40 characters, all seven days as schema.org URLs under
    # 200. Longer text names no weekdays: it stays as written and no pattern
    # sees it - _DAY_RANGE is quadratic in a run of dashes (audit 14.11., L-30).
    MAX_DAY_TEXT = 200
    # "Mo, Mi, Fr", "Sa/So", "Montag und Mittwoch". No blanks in the pattern:
    # the parts are stripped instead.
    _DAY_LIST = re.compile(r"[,;/+&]|\b(?:und|and)\b", re.IGNORECASE)
    # English short forms, only as a whole entry or one end of a range: as
    # words in a sentence ("sat", "sun", "wed") they are no weekdays.
    ENGLISH_SHORT = {
        "mon": "MO",
        "tue": "TU",
        "tues": "TU",
        "wed": "WE",
        "thu": "TH",
        "thur": "TH",
        "thurs": "TH",
        "fri": "FR",
        "sat": "SA",
        "sun": "SU",
    }

    def _get_day_codes(self, day: Any) -> list:
        """
        Short codes for one dayOfWeek entry, expanding a range into its days.

        Matching substrings alone turned "Mo-Fr" into Monday only. A range
        across the weekend ("Fr-Mo") wraps around Sunday. A list in one string
        ("Mo, Mi, Fr", "Mo-Do, Sa") gives every day it names, when each part
        is a day or a range (audit 14.11., L-43). Anything but a string stays
        as it is, for the validator to report.
        """
        if not isinstance(day, str) or len(day) > self.MAX_DAY_TEXT:
            return [day]
        codes = self._range_codes(day)
        if codes:
            return codes
        parts = [part.strip() for part in self._DAY_LIST.split(day) if part.strip()]
        if len(parts) > 1:
            listed = [
                self._range_codes(part) or self._single_code(part) for part in parts
            ]
            if all(listed):
                return list(dict.fromkeys(code for codes in listed for code in codes))
        return [self._get_day_short_code(day)]

    def _range_codes(self, text: str) -> list:
        """The days of "Mo-Fr" in order; [] when the text is no range of two days."""
        match = self._DAY_RANGE.match(text)
        if not match:
            return []
        first, last = (self._get_day_short_code(part) for part in match.groups())
        if first not in self.WEEK or last not in self.WEEK:
            return []
        start, end = self.WEEK.index(first), self.WEEK.index(last)
        length = (end - start) % 7 + 1
        return [self.WEEK[(start + i) % 7] for i in range(length)]

    def _single_code(self, text: str) -> list:
        """[code] when the text names one weekday, else []."""
        code = self._get_day_short_code(text)
        return [code] if code in self.WEEK else []

    def _get_day_short_code(self, day: str) -> str:
        """Convert day name to short code (MO, TU, WE, etc.)."""
        if not day:
            return day

        day_lower = day.lower().strip()

        # Check direct mapping
        if day_lower in self.DAY_MAPPING:
            return self.DAY_MAPPING[day_lower]
        if day_lower.rstrip(".") in self.ENGLISH_SHORT:
            return self.ENGLISH_SHORT[day_lower.rstrip(".")]

        # A day name as a whole word, also "Dienstags" or ".../Monday": as a
        # substring "so" made "Sonderöffnung" a Sunday.
        for key, code in self.DAY_MAPPING.items():
            if re.search(rf"\b{re.escape(key)}s?\b", day_lower):
                return code

        # Already a short code?
        if day.upper() in ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]:
            return day.upper()

        return day

    def _normalize_event_schedule(
        self, schedule: Any, warnings: Optional[list[str]] = None
    ) -> list[dict]:
        """Normalize event schedule with all required fields."""
        if not schedule or not isinstance(schedule, list):
            return schedule if schedule else []

        _warn_about_non_objects("schema:eventSchedule", schedule, warnings)
        result = []
        for item in schedule:
            if not isinstance(item, dict):
                continue
            normalized = item.copy()
            for key in ("startDate", "endDate"):
                if isinstance(normalized.get(key), str):
                    normalized[key] = _iso_date_or_datetime(normalized[key])
            start_date = normalized.get("startDate", "")
            # A number or list is no date to read; it stays for the validator.
            if start_date and isinstance(start_date, str):
                _fill_from_start(normalized, start_date)
            _fill_end(normalized)
            result.append(normalized)
        return result

    def _normalize_access_service(self, services: Any) -> list[dict]:
        """Convert access service strings to objects."""
        if not services:
            return []

        if not isinstance(services, list):
            services = [services]

        result = []
        for item in services:
            if isinstance(item, str):
                # Convert string to object
                if "barrierefrei" in item.lower():
                    result.append(
                        {"serviceType": "Barrierefreiheit", "description": item}
                    )
                elif "ermäßigung" in item.lower():
                    result.append({"serviceType": "Ermäßigung", "description": item})
                else:
                    result.append({"serviceType": "Hinweis", "description": item})
            elif isinstance(item, dict):
                result.append(item)

        return result

    def _normalize_about(self, about: Any) -> list[str]:
        """Normalize about field - convert URIs to labels if needed."""
        if not about:
            return []

        if not isinstance(about, list):
            about = [about]

        result = []
        for item in about:
            if isinstance(item, str):
                # If it's a DBpedia URI, extract the label
                if "dbpedia.org/resource/" in item:
                    label = item.split("/")[-1].replace("_", " ")
                    result.append(label)
                else:
                    result.append(item)
            else:
                result.append(str(item))

        return result


_WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _warn_about_non_objects(
    field_id: str, entries: list, warnings: Optional[list[str]]
) -> None:
    """
    Say that entries which are no objects are left out (audit 14.11., L-N).

    The web component reads objects here; a number or a bare string was
    dropped without a word.
    """
    dropped = sum(1 for entry in entries if not isinstance(entry, dict))
    if dropped and warnings is not None:
        warnings.append(
            f"{field_id}: {dropped} Wert(e) ohne Objektform nicht übernommen."
        )


def _iso_date_or_datetime(text: str) -> str:
    """
    "15.03.2025" and "15.03.2025 14:00" as ISO, for the fields derived from
    them; an ISO value, an impossible date and anything unreadable as written.
    """
    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        return text
    try:
        return read_date(text) or read_datetime(text) or text
    except ImpossibleDate:
        return text


def _fill_from_start(normalized: dict, start_date: str) -> None:
    """byMonth, byMonthDay, byDay and byHour from startDate, unless given; startDate date-only."""
    date_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", start_date)
    time_match = re.search(r"T?(\d{2}):(\d{2})", start_date)
    if date_match:
        year, month, day = date_match.groups()
        if "T" in str(start_date):
            normalized["startDate"] = f"{year}-{month}-{day}"
        normalized.setdefault("byMonth", [int(month)])
        normalized.setdefault("byMonthDay", [int(day)])
        if "byDay" not in normalized:
            try:
                weekday = date(int(year), int(month), int(day)).weekday()
                normalized["byDay"] = [_WEEKDAYS[weekday]]
            except ValueError:
                # An impossible date (2026-02-30): no weekday to derive.
                logger.debug(f"No weekday for impossible date {start_date!r}")
    if time_match:
        normalized.setdefault("byHour", [int(time_match.group(1))])


def _fill_end(normalized: dict) -> None:
    """endDate date-only like startDate; without one, the start date."""
    end_date = normalized.get("endDate", "")
    if isinstance(end_date, str) and "T" in end_date:
        date_match = re.match(r"(\d{4}-\d{2}-\d{2})", end_date)
        if date_match:
            normalized["endDate"] = date_match.group(1)
    elif not end_date and normalized.get("startDate"):
        normalized["endDate"] = normalized["startDate"]


# Singleton instance
_normalizer: Optional[OutputNormalizer] = None


def get_output_normalizer() -> OutputNormalizer:
    """Get singleton instance of output normalizer."""
    global _normalizer
    if _normalizer is None:
        _normalizer = OutputNormalizer()
    return _normalizer
