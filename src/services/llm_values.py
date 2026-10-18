"""Reading a model answer and fitting each value to its field: JSON, booleans, URLs, coordinates, vocabularies."""

import json
import re
from typing import TYPE_CHECKING, Any, Optional

from ..utils.text_utils import levenshtein_within
from ..utils.vocabulary import concept_keys, is_closed_vocabulary
import logging

logger = logging.getLogger(__name__)


class ValueNormalizerMixin:
    """
    Normalizing extracted values by field type (part of LLMService).

    Needs DateNumberMixin and PromptMixin; the methods it uses from them are
    declared below for mypy.
    """

    if TYPE_CHECKING:

        def _normalize_number(self, value: Any, datatype: str) -> Optional[float]: ...
        def _normalize_date(self, value: Any) -> Optional[str]: ...
        def _normalize_datetime(self, value: Any) -> Optional[str]: ...
        def _normalize_time(self, value: Any) -> Optional[str]: ...
        def _get_localized(self, obj: dict[str, str], language: str) -> str: ...

    def _is_valid_vocabulary_value(self, value: Any, vocabulary: dict) -> bool:
        """
        Every entry is a concept of the closed vocabulary.

        A None inside a list is an answer that matched no concept: the list is
        invalid, so extract_field asks again (audit L-07). An empty list is "no
        value", nothing to ask again about.
        """
        if value is None:
            return False

        valid_entries = concept_keys(vocabulary.get("concepts", []))
        if isinstance(value, list):
            return all(v is not None and v in valid_entries for v in value)
        return value in valid_entries

    def _parse_json_response(self, content: str) -> Optional[dict[str, Any]]:
        """Parse JSON from LLM response, handling various formats."""
        try:
            json_str = content.strip()

            # Remove markdown code blocks if present
            code_block_match = re.search(r"```json\s*([\s\S]*?)\s*```", json_str)
            if code_block_match:
                json_str = code_block_match.group(1).strip()

            # Try to parse directly. A bare value ("Titel", ["a", "b"]) is the
            # answer itself — callers read result["value"], so wrap it.
            try:
                parsed = json.loads(json_str)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                pass

            # Try to extract JSON patterns
            array_match = re.search(r"\[[\s\S]*\]", json_str)
            object_match = re.search(r"\{[\s\S]*\}", json_str)

            if object_match:
                return json.loads(object_match.group(0))
            if array_match:
                return {"value": json.loads(array_match.group(0))}

            # Try to extract string value
            string_match = re.search(r'"([^"]+)"', json_str)
            if string_match:
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
            return self._validate_vocabulary(value, vocabulary)

        # Boolean normalization
        if datatype == "boolean":
            return self._normalize_boolean(value)

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
        if datatype in ("uri", "url"):
            return self._normalize_url(value)

        # Geo coordinate validation
        if "latitude" in field_id.lower() or "longitude" in field_id.lower():
            return self._normalize_geo_coordinate(value, field_id)

        # Vocabulary validation
        if vocabulary and vocabulary.get("concepts"):
            return self._validate_vocabulary(value, vocabulary)

        return value

    def _normalize_boolean(self, value: Any) -> Optional[bool]:
        """Normalize boolean values."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            val = value.lower().strip()
            if val in ("ja", "yes", "wahr", "true", "1"):
                return True
            if val in ("nein", "no", "falsch", "false", "0"):
                return False
        if isinstance(value, (int, float)):
            return value != 0
        return None

    def _normalize_geo_coordinate(self, value: Any, field_id: str) -> Optional[float]:
        """Normalize and validate geo coordinates (latitude/longitude)."""
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

    def _normalize_url(self, value: Any) -> Optional[str]:
        """Normalize URL, adding protocol if missing."""
        if not isinstance(value, str):
            return None

        val = value.strip()

        # Already has protocol
        if re.match(r"^https?://", val, re.IGNORECASE):
            return val

        # Add https:// if looks like URL
        if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z]{2,}", val):
            return f"https://{val}"

        return val

    def _validate_vocabulary(self, value: Any, vocabulary: dict) -> Any:
        """Validate value against vocabulary, return URI or original value."""
        concepts = vocabulary.get("concepts", [])
        is_closed = is_closed_vocabulary(vocabulary)

        if not concepts:
            return value

        value_lower = str(value).lower().strip()

        # Check if already a valid URI or value.
        # Numeric scales ('0'…'5') and the 'true'/'false' flags come back from the
        # LLM as JSON numbers and booleans just as often as strings, so compare on
        # the lowercased string form — str(True) is 'True', not 'true' — and
        # return the value as the vocabulary spells it.
        uri_match = next((c for c in concepts if c.get("uri") == value), None)
        if uri_match:
            return value
        value_match = next(
            (
                c
                for c in concepts
                if c.get("value") is not None and str(c["value"]).lower() == value_lower
            ),
            None,
        )
        if value_match:
            return value_match["value"]

        # Find by label (exact match)
        for concept in concepts:
            label = self._get_localized(concept.get("label", {}), "de")
            if label.lower() == value_lower:
                return concept.get("uri", concept.get("value", label))

            # Check alt labels
            alt_labels = concept.get("altLabels", [])
            for alt in alt_labels:
                if alt.lower() == value_lower:
                    return concept.get("uri", concept.get("value", label))

        # For closed vocabulary, try fuzzy matching
        if is_closed:
            best_match = self._fuzzy_match_vocabulary(value_lower, concepts)
            if best_match:
                return best_match
            return None  # Invalid value for closed vocabulary

        return value  # Open vocabulary: keep original

    def _fuzzy_match_vocabulary(self, value: str, concepts: list) -> Optional[str]:
        """Fuzzy match value against vocabulary concepts."""
        # Accept if distance is small (max 30% of string length, 1 to 3 edits)
        limit = min(3, max(1, int(len(value) * 0.3)))
        best_match = None
        best_distance = limit + 1

        for concept in concepts:
            label = self._get_localized(concept.get("label", {}), "de").lower()
            distance = levenshtein_within(value, label, limit)
            if distance is not None and distance < best_distance:
                best_distance = distance
                best_match = concept

        if best_match:
            return best_match.get(
                "uri", self._get_localized(best_match.get("label", {}), "de")
            )
        return None
