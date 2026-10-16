"""
Field Normalizer Service for metadata API.
Normalizes user input and extracted values based on field schema.
Ported from metadata-agent-canvas-optimized FieldNormalizerService.
"""

import re
from typing import Any, Optional
from datetime import date

from ..utils.text_utils import levenshtein_distance


class FieldNormalizer:
    """Normalizes field values based on schema definitions."""

    # German number words
    GERMAN_NUMBERS = {
        "null": 0,
        "eins": 1,
        "zwei": 2,
        "drei": 3,
        "vier": 4,
        "fünf": 5,
        "sechs": 6,
        "sieben": 7,
        "acht": 8,
        "neun": 9,
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
        "zwanzig": 20,
        "dreißig": 30,
        "vierzig": 40,
        "fünfzig": 50,
        "sechzig": 60,
        "siebzig": 70,
        "achtzig": 80,
        "neunzig": 90,
        "hundert": 100,
        "tausend": 1000,
    }

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
    }

    def normalize_field_value(
        self, value: Any, field_schema: dict, normalize_vocabularies: bool = True
    ) -> Any:
        """
        Normalize a single field value based on its schema.

        Args:
            value: The value to normalize
            field_schema: The field's schema definition
            normalize_vocabularies: Whether to normalize vocabulary values

        Returns:
            Normalized value
        """
        if value is None or value == "":
            return value

        datatype = field_schema.get("datatype", "string")
        vocabulary = field_schema.get("vocabulary")
        # datatype "array" is a list whatever `multiple` says (core.json declares
        # ccm:oeh_extendedType as array with multiple false).
        is_multiple = field_schema.get("multiple", False) or datatype == "array"

        # Object fields decide their shape from the schema, never from the value.
        # A bare string here is what fixes the Elasticsearch path to `text` and
        # locks every correctly structured record out of the index afterwards.
        if self._is_object_field(field_schema):
            return self._normalize_object_field(value, field_schema)

        # Handle arrays
        if isinstance(value, list):
            if is_multiple:
                return [
                    self._normalize_single_value(
                        v, datatype, vocabulary, normalize_vocabularies
                    )
                    for v in value
                    if v is not None and v != ""
                ]
            else:
                # Field is not multiple but value is a list — unwrap first element
                if len(value) == 0:
                    return None
                value = value[0]
                if value is None or value == "":
                    return value

        return self._normalize_single_value(
            value, datatype, vocabulary, normalize_vocabularies
        )

    def _is_object_field(self, field_schema: dict) -> bool:
        """Does the SCHEMA say this field holds objects?

        All four spellings count. The schemas were unified on
        `datatype: array` + `items.datatype: object`, but older releases still
        carry `datatype: object` with `multiple: true` and the variants at the
        system level, and both are read here.
        """
        items = field_schema.get("items") or {}
        return (
            field_schema.get("datatype") == "object"
            or items.get("datatype") == "object"
            or bool(items.get("variants"))
            or bool(field_schema.get("variants"))
        )

    def _object_key(self, field_schema: dict) -> str:
        """The sub-field a bare string belongs in.

        `name` when the variant declares one — that is the schema.org convention
        and what the old `_normalize_person_array` produced. Otherwise the first
        declared sub-field, which for `ContactPoint` is `contactType`, matching
        how the organization records in the repository already use it.
        """
        items = field_schema.get("items") or {}
        for variant in items.get("variants") or field_schema.get("variants") or []:
            names = [f.get("id") for f in (variant.get("fields") or []) if f.get("id")]
            if "name" in names:
                return "name"
            if names:
                return names[0]
        return "name"

    def _normalize_object_field(self, value: Any, field_schema: dict) -> Any:
        is_list = bool(
            field_schema.get("datatype") == "array" or field_schema.get("multiple")
        )
        key = self._object_key(field_schema)

        if isinstance(value, list):
            items = [
                self._as_object(v, key) for v in value if v is not None and v != ""
            ]
            return [item for item in items if item is not None]

        obj = self._as_object(value, key)
        if obj is None:
            return [] if is_list else None
        return [obj] if is_list else obj

    def _as_object(self, value: Any, key: str) -> Optional[dict]:
        """Lift a single value into the object shape the schema asks for."""
        if isinstance(value, dict):
            # `@value` is what some LLM answers use instead of a named field.
            if "name" not in value and "@value" in value:
                value = dict(value)
                value["name"] = value.pop("@value")
            return value
        if isinstance(value, str):
            text = value.strip()
            return {key: text} if text else None
        # Numbers and booleans are not recoverable into a named object — leave
        # them for the validator to report instead of inventing a shape.
        return value

    def _normalize_single_value(
        self,
        value: Any,
        datatype: str,
        vocabulary: Optional[dict],
        normalize_vocabularies: bool,
    ) -> Any:
        """Normalize a single (non-array) value."""

        # Vocabulary normalization
        if vocabulary and normalize_vocabularies:
            normalized = self._normalize_vocabulary(value, vocabulary)
            if normalized is not None:
                return normalized

        # Type-specific normalization
        if datatype == "boolean":
            return self._normalize_boolean(value)
        elif datatype in ("number", "integer"):
            return self._normalize_number(value, datatype == "integer")
        elif datatype == "date":
            return self._normalize_date(value)
        elif datatype == "datetime":
            return self._normalize_datetime(value)
        elif datatype == "time":
            return self._normalize_time(value)
        elif datatype in ("uri", "url"):
            return self._normalize_url(value)
        elif datatype == "duration":
            return self._normalize_duration(value)

        # Default: return as-is (strings)
        return value

    def _normalize_boolean(self, value: Any) -> Optional[bool]:
        """Normalize boolean values."""
        if isinstance(value, bool):
            return value
        if not isinstance(value, str):
            return None

        s = value.lower().strip()
        if s in ("true", "ja", "yes", "wahr", "1"):
            return True
        if s in ("false", "nein", "no", "falsch", "0"):
            return False
        return None

    def _normalize_number(
        self, value: Any, as_integer: bool = False
    ) -> Optional[float | int]:
        """Normalize number values."""
        if isinstance(value, (int, float)):
            return int(value) if as_integer else value
        if not isinstance(value, str):
            return None

        s = value.lower().strip().replace(",", ".")

        # Try German number words
        if s in self.GERMAN_NUMBERS:
            return self.GERMAN_NUMBERS[s]

        try:
            num = float(s)
            return int(num) if as_integer else num
        except ValueError:
            return None

    def _normalize_date(self, value: Any) -> Optional[str]:
        """Normalize date to ISO format (YYYY-MM-DD)."""
        if not isinstance(value, str):
            return None

        s = value.strip()

        # Already ISO format
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return s

        # German format DD.MM.YYYY
        formats = [
            (r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", True),  # DD.MM.YYYY
            (r"^(\d{1,2})/(\d{1,2})/(\d{4})$", True),  # DD/MM/YYYY
            (r"^(\d{1,2})-(\d{1,2})-(\d{4})$", True),  # DD-MM-YYYY
        ]

        for pattern, day_first in formats:
            match = re.match(pattern, s)
            if match:
                day = int(match.group(1))
                month = int(match.group(2))
                year = int(match.group(3))

                if 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                    try:
                        d = date(year, month, day)
                        return d.isoformat()
                    except ValueError:
                        pass

        # Try parsing ISO with time
        match = re.match(r"^(\d{4}-\d{2}-\d{2})T", s)
        if match:
            return match.group(1)

        return s  # Return original value instead of None to prevent data loss

    def _normalize_datetime(self, value: Any) -> Optional[str]:
        """Normalize datetime to ISO format."""
        if not isinstance(value, str):
            return None

        s = value.strip()

        # Already ISO format with time
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?", s):
            # Remove seconds if present
            match = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", s)
            return match.group(1) if match else s

        # Date only - add midnight
        date_normalized = self._normalize_date(s)
        if date_normalized and date_normalized != s:
            return f"{date_normalized}T00:00"

        return s  # Return original value instead of None to prevent data loss

    def _normalize_time(self, value: Any) -> Optional[str]:
        """Normalize time to HH:MM format."""
        if not isinstance(value, str):
            return None

        s = value.strip()

        match = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", s)
        if match:
            hours = int(match.group(1))
            minutes = int(match.group(2))
            if 0 <= hours <= 23 and 0 <= minutes <= 59:
                return f"{hours:02d}:{minutes:02d}"

        return s  # Return original value instead of None to prevent data loss

    def _normalize_url(self, value: Any) -> Optional[str]:
        """Normalize URL - add https:// if missing."""
        if not isinstance(value, str):
            return None

        s = value.strip()

        if re.match(r"^https?://", s, re.IGNORECASE):
            return s

        # Looks like a domain
        if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z]{2,}", s):
            return f"https://{s}"

        return s

    def _normalize_duration(self, value: Any) -> Optional[str]:
        """Normalize duration to ISO 8601 format."""
        if not isinstance(value, str):
            return None

        s = value.strip().upper()

        # Already ISO format
        if re.match(r"^P(\d+[YMWD])*T?(\d+[HMS])*$", s) and s != "P" and s != "PT":
            return s

        s_lower = value.lower().strip()

        # Parse common formats
        patterns = [
            (r"^(\d+)\s*(stunden?|hours?|h)$", lambda m: f"PT{m.group(1)}H"),
            (r"^(\d+)\s*(minuten?|minutes?|min|m)$", lambda m: f"PT{m.group(1)}M"),
            (r"^(\d+)\s*(tage?|days?|d)$", lambda m: f"P{m.group(1)}D"),
            (r"^(\d+)\s*(wochen?|weeks?|w)$", lambda m: f"P{m.group(1)}W"),
        ]

        for pattern, formatter in patterns:
            match = re.match(pattern, s_lower)
            if match:
                return formatter(match)

        return None

    def _normalize_vocabulary(self, value: Any, vocabulary: dict) -> Optional[Any]:
        """Normalize value against vocabulary concepts."""
        if not value:
            return None

        concepts = vocabulary.get("concepts", [])
        vocab_type = vocabulary.get("type", "open")

        if not concepts:
            return value

        # Check if concepts have URIs or plain values
        has_uris = any(c.get("uri") for c in concepts)
        has_values = any(c.get("value") for c in concepts)

        # Handle array values
        if isinstance(value, list):
            normalized = [
                self._match_concept(v, concepts, has_uris, has_values, vocab_type)
                for v in value
            ]
            if vocab_type == "closed":
                return [n for n in normalized if n is not None]
            return normalized

        return self._match_concept(value, concepts, has_uris, has_values, vocab_type)

    def _match_concept(
        self,
        value: str,
        concepts: list,
        has_uris: bool,
        has_values: bool,
        vocab_type: str,
    ) -> Optional[str]:
        """Match a single value against vocabulary concepts."""
        if not isinstance(value, str):
            return value

        value_lower = value.lower().strip()

        # Exact URI match
        if has_uris:
            for concept in concepts:
                if concept.get("uri") == value:
                    return value

        # Exact value match (e.g. language codes like 'de', 'en')
        if has_values:
            for concept in concepts:
                if concept.get("value") == value:
                    return value

        # Exact label match
        for concept in concepts:
            label = concept.get("label", "")
            if isinstance(label, dict):
                label = label.get("de", label.get("en", ""))

            if label.lower() == value_lower:
                if has_uris and concept.get("uri"):
                    return concept.get("uri")
                if has_values and concept.get("value"):
                    return concept.get("value")
                return label

            # Check altLabels
            alt_labels = concept.get("altLabels", [])
            for alt in alt_labels:
                if alt.lower() == value_lower:
                    if has_uris and concept.get("uri"):
                        return concept.get("uri")
                    if has_values and concept.get("value"):
                        return concept.get("value")
                    return label

        # Fuzzy match for closed vocabularies
        if vocab_type == "closed":
            best_match = self._find_fuzzy_match(value_lower, concepts)
            if best_match:
                if has_uris and best_match.get("uri"):
                    return best_match.get("uri")
                if has_values and best_match.get("value"):
                    return best_match.get("value")
                return best_match.get("label")
            return None

        return value

    def _find_fuzzy_match(self, value: str, concepts: list) -> Optional[dict]:
        """Find closest matching concept using Levenshtein distance."""
        best_match = None
        best_distance = float("inf")

        for concept in concepts:
            label = concept.get("label", "")
            if isinstance(label, dict):
                label = label.get("de", label.get("en", ""))

            distance = self._levenshtein_distance(value, label.lower())
            if distance < best_distance:
                best_distance = distance
                best_match = concept

        # Only accept if distance is within 30% of string length or max 3
        max_distance = min(3, len(value) * 0.3)
        if best_match and best_distance <= max_distance:
            return best_match

        return None

    def _levenshtein_distance(self, a: str, b: str) -> int:
        """Calculate Levenshtein edit distance."""
        return levenshtein_distance(a, b)

    def normalize_day_of_week(self, value: str) -> str:
        """Convert day name to short code (MO, TU, etc.)."""
        if not value:
            return value

        lower = value.lower().strip()
        return self.DAY_MAPPING.get(lower, value.upper() if len(value) == 2 else value)


# Singleton instance
_normalizer: Optional[FieldNormalizer] = None


def get_field_normalizer() -> FieldNormalizer:
    """Get singleton instance of field normalizer."""
    global _normalizer
    if _normalizer is None:
        _normalizer = FieldNormalizer()
    return _normalizer


def find_object_shape_violations(
    metadata: dict[str, Any], context: str, version: str, schema_file: str
) -> list[dict[str, Any]]:
    """Name the fields whose value does not have the shape the schema declares.

    Reports, it does NOT repair. An earlier version lifted the value into shape
    here, which meant the repository received something the editor had never
    seen on screen — a metadata tool must write what was reviewed and approved,
    not a corrected version of it. Repair belongs where the result is still
    visible and can be rejected: the web component does it at the input, and
    FieldNormalizer does it to the LLM output before the canvas shows it.

    At this point the only honest options are to write the value as it is, which
    poisons the Elasticsearch mapping, or to refuse the write and say which
    field is wrong. This function provides the second.

    Never raises. A schema that cannot be loaded yields no findings — refusing a
    write because a schema file is missing would be worse than writing a value
    whose shape nobody could check.
    """
    from ..utils.schema_loader import load_schema

    try:
        schema = load_schema(context, version, schema_file)
        fields = list(schema.get("fields") or [])
        if schema_file != "core.json":
            try:
                core = load_schema(context, version, "core.json")
                known = {f.get("id") for f in fields}
                fields += [
                    f for f in (core.get("fields") or []) if f.get("id") not in known
                ]
            except Exception:
                pass
    except Exception:
        return []

    normalizer = get_field_normalizer()
    violations: list[dict[str, Any]] = []

    for field in fields:
        field_id = field.get("id")
        system = field.get("system") or {}
        if field_id not in metadata or not normalizer._is_object_field(system):
            continue

        value = metadata[field_id]
        if value is None or value == "" or value == []:
            continue

        items = value if isinstance(value, list) else [value]
        if all(isinstance(item, dict) for item in items):
            continue

        key = normalizer._object_key(system)
        violations.append(
            {
                "field_id": field_id,
                "value": value,
                "expected_key": key,
                "message": (
                    f"{field_id}: erwartet ein Objekt, bekommen {value!r}. "
                    f'Richtig wäre z.B. {{"{key}": "..."}}.'
                ),
            }
        )

    return violations
