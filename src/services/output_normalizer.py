"""
Output normalizer for metadata API.
Transforms API output to match the structure expected by the Canvas web component.
"""

import logging
import re
from datetime import date
from typing import Any, Optional

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

    def normalize_output(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize all metadata fields to match Canvas web component structure.
        """
        result = metadata.copy()

        # Note: lifting bare strings into objects is NOT done here any more.
        # This used to be a list of six field names, which covered 4 of the 37
        # object-typed fields the schemas declare — person and organization had
        # no coverage at all, and that is where the staging damage happened.
        # FieldNormalizer._is_object_field now derives it from the schema and
        # runs before this method. `_normalize_person_array` is kept because it
        # defines the object key convention the two must agree on; see
        # tests/test_output_normalizer_list_is_redundant.py.

        # Opening hours: normalize dayOfWeek to array with short codes
        if "schema:openingHoursSpecification" in result:
            result["schema:openingHoursSpecification"] = self._normalize_opening_hours(
                result["schema:openingHoursSpecification"]
            )

        # Event schedule: add missing fields
        if "schema:eventSchedule" in result:
            result["schema:eventSchedule"] = self._normalize_event_schedule(
                result["schema:eventSchedule"]
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

    def _normalize_person_array(self, value: Any) -> list[dict]:
        """Convert person values to objects with name field."""
        if not value:
            return []

        if not isinstance(value, list):
            value = [value]

        result = []
        for item in value:
            if isinstance(item, str):
                result.append({"name": item})
            elif isinstance(item, dict):
                if "name" not in item and "@value" in item:
                    item["name"] = item.pop("@value")
                result.append(item)

        return result

    def _normalize_opening_hours(self, hours: Any) -> list[dict]:
        """Normalize opening hours specification."""
        if not hours or not isinstance(hours, list):
            return hours if hours else []

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

    def _get_day_codes(self, day: Any) -> list:
        """
        Short codes for one dayOfWeek entry, expanding a range into its days.

        Matching substrings alone turned "Mo-Fr" into Monday only.
        """
        if isinstance(day, str):
            match = self._DAY_RANGE.match(day)
            if match:
                first, last = (
                    self._get_day_short_code(part) for part in match.groups()
                )
                if first in self.WEEK and last in self.WEEK:
                    start, end = self.WEEK.index(first), self.WEEK.index(last)
                    if start <= end:
                        return self.WEEK[start : end + 1]
        return [self._get_day_short_code(day)]

    def _get_day_short_code(self, day: str) -> str:
        """Convert day name to short code (MO, TU, WE, etc.)."""
        if not day:
            return day

        day_lower = day.lower().strip()

        # Check direct mapping
        if day_lower in self.DAY_MAPPING:
            return self.DAY_MAPPING[day_lower]

        # Try to extract from various formats
        for key, code in self.DAY_MAPPING.items():
            if key in day_lower:
                return code

        # Already a short code?
        if day.upper() in ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]:
            return day.upper()

        return day

    def _normalize_event_schedule(self, schedule: Any) -> list[dict]:
        """Normalize event schedule with all required fields."""
        if not schedule or not isinstance(schedule, list):
            return schedule if schedule else []

        result = []
        for item in schedule:
            if not isinstance(item, dict):
                continue
            normalized = item.copy()
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
