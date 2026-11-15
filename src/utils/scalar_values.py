"""
Booleans and URLs as written in model answers and by callers - one
implementation for the model answer path (llm_values) and FieldNormalizer
(audit 14.11.2026, Q-15).

The two had a copy each. The URL copies were identical; the boolean copies
disagreed about numbers: the model path took any number but 0 as true (7 was
true), FieldNormalizer took none (1 was no boolean). Decided once: the numbers
1 and 0 are true and false, as their strings "1" and "0" always were; any
other number is no boolean.
"""

import re
from typing import Any, Optional

_TRUE = ("true", "ja", "yes", "wahr", "1")
_FALSE = ("false", "nein", "no", "falsch", "0")


def normalize_boolean(value: Any) -> Optional[bool]:
    """True / False for a boolean, a yes-no word or the numbers 1 and 0; else None."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value) if value in (0, 1) else None
    if not isinstance(value, str):
        return None
    word = value.lower().strip()
    if word in _TRUE:
        return True
    if word in _FALSE:
        return False
    return None


def normalize_url(value: Any) -> Optional[str]:
    """The URL, with https:// in front of what looks like a bare domain; None for a non-string."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if re.match(r"^https?://", url, re.IGNORECASE):
        return url
    if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z]{2,}", url):
        return f"https://{url}"
    return url
