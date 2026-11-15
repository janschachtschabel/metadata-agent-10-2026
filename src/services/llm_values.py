"""Reading a model answer and fitting each value to its field: JSON, booleans, URLs, coordinates, vocabularies."""

import json
import math
import re
from typing import TYPE_CHECKING, Any, Optional

from ..utils.scalar_values import normalize_boolean, normalize_url
from ..utils.vocabulary import is_closed_vocabulary
from .vocabulary_matching import match_model_answer
import logging

logger = logging.getLogger(__name__)


def _between(text: str, opening: str, closing: str) -> str:
    """From the first `opening` to the last `closing` after it; "" without both."""
    start = text.find(opening)
    end = text.rfind(closing)
    return text[start : end + 1] if 0 <= start < end else ""


class ValueNormalizerMixin:
    """
    Normalizing extracted values by field type (part of LLMService).

    Needs DateNumberMixin; the methods it uses from it are declared below for
    mypy. Vocabularies are matched in vocabulary_matching.
    """

    if TYPE_CHECKING:

        def _normalize_number(self, value: Any, datatype: str) -> Optional[float]: ...
        def _normalize_date(self, value: Any) -> Optional[str]: ...
        def _normalize_datetime(self, value: Any) -> Optional[str]: ...
        def _normalize_time(self, value: Any) -> Optional[str]: ...

    def _parse_json_response(self, content: str) -> Optional[dict[str, Any]]:
        """Parse JSON from LLM response, handling various formats."""
        try:
            json_str = content.strip()

            # Remove markdown code blocks if present
            # The blanks around the block are stripped below, not matched:
            # between two more quantifiers they cost cubic time (audit P-08).
            code_block_match = re.search(r"```json([\s\S]*?)```", json_str)
            if code_block_match:
                json_str = code_block_match.group(1).strip()

            # Try to parse directly. A bare value ("Titel", ["a", "b"]) is the
            # answer itself — callers read result["value"], so wrap it.
            try:
                parsed = json.loads(json_str)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                pass

            # JSON inside a sentence: from the first opening to the last closing
            # bracket. Not a pattern - "\{[\s\S]*\}" was quadratic in a run of
            # "{" (audit 14.11., L-42).
            embedded_object = _between(json_str, "{", "}")
            if embedded_object:
                return json.loads(embedded_object)
            embedded_array = _between(json_str, "[", "]")
            if embedded_array:
                return {"value": json.loads(embedded_array)}

            # A quoted string in a plain sentence. Not in what looks like
            # JSON: of a broken `{"value": "…` the first quoted text is the key.
            string_match = re.search(r'"([^"]+)"', json_str)
            if string_match and not ("{" in json_str or "[" in json_str):
                return {"value": string_match.group(1)}

            # Check for null
            if json_str.lower() == "null":
                return {"value": None}

            # Check for number
            try:
                return {"value": float(json_str) if "." in json_str else int(json_str)}
            except ValueError:
                pass

            return None

        except Exception as e:
            logger.error(f"❌ JSON parse error: {e}")
            return None

    def _normalize_value(self, value: Any, field: dict[str, Any]) -> Any:
        """
        Normalize extracted value based on field schema.
        Handles: booleans, numbers, dates, datetimes, times, URLs,
        geo coordinates, vocabulary matching.
        """
        if value is None:
            return None

        system = field.get("system", {})
        datatype = system.get("datatype", "string")
        vocabulary = system.get("vocabulary", {})
        field_id = field.get("id", "")

        # Handle arrays
        if isinstance(value, list):
            return [
                self._normalize_single_value(v, datatype, vocabulary, field_id)
                for v in value
            ]

        return self._normalize_single_value(value, datatype, vocabulary, field_id)

    def _normalize_single_value(
        self, value: Any, datatype: str, vocabulary: dict, field_id: str = ""
    ) -> Any:
        """Normalize a single value."""
        if value is None:
            return None

        # A closed vocabulary decides before the datatype, as in FieldNormalizer:
        # dpt:concept is a uri field, and a label answer is matched to its
        # concept URI instead of being kept as a "URL" that no concept has.
        if is_closed_vocabulary(vocabulary):
            return match_model_answer(value, vocabulary)

        # Boolean normalization
        if datatype == "boolean":
            return normalize_boolean(value)

        # Number normalization
        if datatype in ("number", "integer"):
            return self._normalize_number(value, datatype)

        # Date normalization
        if datatype == "date":
            return self._normalize_date(value)

        # Datetime normalization
        if datatype == "datetime":
            return self._normalize_datetime(value)

        # Time normalization
        if datatype == "time":
            return self._normalize_time(value)

        # URL normalization
        if datatype == "uri":
            return normalize_url(value)

        # Geo coordinate validation
        if "latitude" in field_id.lower() or "longitude" in field_id.lower():
            return self._normalize_geo_coordinate(value, field_id)

        # Vocabulary validation
        if vocabulary and vocabulary.get("concepts"):
            return match_model_answer(value, vocabulary)

        return value

    def _normalize_geo_coordinate(self, value: Any, field_id: str) -> Optional[float]:
        """Normalize and validate geo coordinates (latitude/longitude)."""
        # A boolean is no coordinate (True would be 1.0).
        if isinstance(value, bool):
            return None

        # Already a number
        if isinstance(value, (int, float)):
            return self._validate_geo_range(float(value), field_id)

        if isinstance(value, str):
            val = value.strip()

            # Handle German decimal separator
            val = val.replace(",", ".")

            # Remove degree symbols
            val = re.sub(r"[°º]", "", val)

            try:
                num = float(val)
                return self._validate_geo_range(num, field_id)
            except ValueError:
                pass

        return None

    def _validate_geo_range(self, value: float, field_id: str) -> Optional[float]:
        """Validate geo coordinate range and round to 7 decimal places."""
        # NaN passes every comparison below and is no JSON number.
        if not math.isfinite(value):
            return None

        is_latitude = "latitude" in field_id.lower() or "lat" in field_id.lower()
        is_longitude = (
            "longitude" in field_id.lower()
            or "lon" in field_id.lower()
            or "lng" in field_id.lower()
        )

        if is_latitude:
            if value < -90 or value > 90:
                return None
        elif is_longitude:
            if value < -180 or value > 180:
                return None

        # Round to 7 decimal places (~1cm precision)
        return round(value, 7)
