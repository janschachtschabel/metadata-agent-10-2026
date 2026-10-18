"""Numbers (also German number words), dates, date-times and times from model answers."""

from typing import Any, Optional

from ..utils.dates_numbers import (
    ImpossibleDate,
    read_date,
    read_datetime,
    read_time,
    to_number,
)


class DateNumberMixin:
    """
    Normalizing numbers, dates and times (part of LLMService).

    The rules live in utils/dates_numbers.py, shared with FieldNormalizer.
    Needs: nothing from the other mixins or from LLMService.__init__.
    """

    def _normalize_number(self, value: Any, datatype: str) -> Optional[float]:
        """A finite number, also from German number words; else None."""
        return to_number(value, integer=datatype == "integer")

    def _normalize_date(self, value: Any) -> Optional[str]:
        """YYYY-MM-DD; an impossible date gives None, an unreadable one stays as written."""
        if not isinstance(value, str):
            return None
        try:
            return read_date(value) or value.strip()
        except ImpossibleDate:
            return None

    def _normalize_datetime(self, value: Any) -> Optional[str]:
        """YYYY-MM-DDTHH:MM; an unreadable value stays as written."""
        if not isinstance(value, str):
            return None
        return read_datetime(value) or value.strip()

    def _normalize_time(self, value: Any) -> Optional[str]:
        """HH:MM; an unreadable value stays as written."""
        if not isinstance(value, str):
            return None
        return read_time(value) or value.strip()
