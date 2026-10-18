"""
Field Normalizer Service for metadata API.
Normalizes user input and extracted values based on field schema.
Ported from metadata-agent-canvas-optimized FieldNormalizerService.
"""

import logging
import re
from typing import Any, Optional

from ..utils.dates_numbers import (
    ImpossibleDate,
    read_date,
    read_datetime,
    read_time,
    to_number,
)
from ..utils.text_utils import levenshtein_within
from ..utils.vocabulary import is_closed_vocabulary

logger = logging.getLogger(__name__)


class FieldNormalizer:
    """Normalizes field values based on schema definitions."""

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
        self,
        value: Any,
        field_schema: dict,
        normalize_vocabularies: bool = True,
        exact_only: bool = False,
    ) -> Any:
        """
        Normalize a single field value based on its schema.

        Args:
            value: The value to normalize
            field_schema: The field's schema definition
            normalize_vocabularies: Whether to normalize vocabulary values
            exact_only: For values the caller or the node brings: a vocabulary
                value is matched only exactly (URI, value, label or altLabel
                in any language) and kept unchanged when nothing matches

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
                # An entry that normalises to None (no concept, no number) is
                # no value: dropped rather than written as null.
                normalized = (
                    self._normalize_single_value(
                        v, datatype, vocabulary, normalize_vocabularies, exact_only
                    )
                    for v in value
                    if v is not None and v != ""
                )
                return [v for v in normalized if v is not None]
            else:
                # Field is not multiple but value is a list — unwrap first element
                if len(value) == 0:
                    return None
                value = value[0]
                if value is None or value == "":
                    return value

        return self._normalize_single_value(
            value, datatype, vocabulary, normalize_vocabularies, exact_only
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
        exact_only: bool = False,
    ) -> Any:
        """Normalize a single (non-array) value."""

        # The caller's own values: exact matches only, unknown ones stay - a
        # legacy concept the repository still holds is no reason to drop it
        # (final audit 18.10.), and no fuzzy search over unbounded lists.
        if exact_only and vocabulary and normalize_vocabularies:
            if vocabulary.get("concepts"):
                return self._exact_concept(value, vocabulary["concepts"])

        # Vocabulary normalization
        if vocabulary and normalize_vocabularies:
            normalized = self._normalize_vocabulary(value, vocabulary)
            # A closed vocabulary allows only its concepts (audit L-08).
            if normalized is not None or is_closed_vocabulary(vocabulary):
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
        """A finite number, also from German number words; else None."""
        return to_number(value, integer=as_integer)

    def _normalize_date(self, value: Any) -> Optional[str]:
        """YYYY-MM-DD; a value it cannot read (or an impossible date) stays as written."""
        if not isinstance(value, str):
            return None
        try:
            return read_date(value) or value.strip()
        except ImpossibleDate:
            return value.strip()

    def _normalize_datetime(self, value: Any) -> Optional[str]:
        """YYYY-MM-DDTHH:MM; a value it cannot read stays as written."""
        if not isinstance(value, str):
            return None
        return read_datetime(value) or value.strip()

    def _normalize_time(self, value: Any) -> Optional[str]:
        """HH:MM; a value it cannot read stays as written."""
        if not isinstance(value, str):
            return None
        return read_time(value) or value.strip()

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
        # Not `not value`: 0 and False are concepts of the quality and AI scales.
        if value is None or value == "" or value == []:
            return None

        concepts = vocabulary.get("concepts", [])
        vocab_type = "closed" if is_closed_vocabulary(vocabulary) else "open"

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
        value: Any,
        concepts: list,
        has_uris: bool,
        has_values: bool,
        vocab_type: str,
    ) -> Any:
        """Match a single value against vocabulary concepts."""
        if not isinstance(value, str):
            if vocab_type != "closed":
                return value
            if not isinstance(value, (bool, int, float)):
                return None  # an object or a list is no concept
            # JSON numbers and booleans by their string form, as in the LLM path
            # (llm_values._validate_vocabulary): 0 -> "0", True -> "true".
            value = str(value).lower()

        value_lower = value.lower().strip()

        # Already a concept: its URI or value (language codes like 'de', 'en')
        if (has_uris and any(c.get("uri") == value for c in concepts)) or (
            has_values and any(c.get("value") == value for c in concepts)
        ):
            return value

        # Exact label or altLabel
        for concept in concepts:
            label = _german_label(concept)
            alt_labels = [alt.lower() for alt in concept.get("altLabels", [])]
            if label.lower() == value_lower or value_lower in alt_labels:
                return _concept_key(concept, has_uris, has_values, label)

        if vocab_type != "closed":
            return value
        best_match = self._find_fuzzy_match(value_lower, concepts)
        if best_match is None:
            return None
        return _concept_key(best_match, has_uris, has_values, best_match.get("label"))

    def _exact_concept(self, value: Any, concepts: list) -> Any:
        """The concept a value names exactly (as 2.1.1 matched), else the value itself."""
        exact, labels = self._exact_index(concepts)
        if isinstance(value, str):
            text = value.strip()
            found = exact.get(value, exact.get(text, labels.get(text.lower())))
        elif isinstance(value, (bool, int, float)):
            found = exact.get(str(value).lower())
        else:
            found = None
        return value if found is None else found

    def _exact_index(self, concepts: list) -> tuple[dict, dict]:
        """
        URI/value -> concept key and lower-case label/altLabel -> concept key.

        Built once per vocabulary and kept for the next entries of the same
        list: 1,000 caller values against 894 concepts stay a dictionary lookup.
        """
        cached = getattr(self, "_exact_cache", None)
        if cached is not None and cached[0] is concepts:
            return cached[1]
        has_uris = any(c.get("uri") for c in concepts)
        has_values = any(c.get("value") for c in concepts)
        exact: dict = {}
        labels: dict = {}
        for concept in concepts:
            key = _concept_key(concept, has_uris, has_values, _german_label(concept))
            for own in (concept.get("uri"), concept.get("value")):
                if own:
                    exact.setdefault(own, key)
            label = concept.get("label", "")
            names = list(label.values()) if isinstance(label, dict) else [label]
            for name in [*names, *concept.get("altLabels", [])]:
                if isinstance(name, str) and name.strip():
                    labels.setdefault(name.strip().lower(), key)
        self._exact_cache = (concepts, (exact, labels))
        return exact, labels

    def _find_fuzzy_match(self, value: str, concepts: list) -> Optional[dict]:
        """The concept whose label is closest, within 30 % of the length and at most 3 edits."""
        limit = min(3, int(len(value) * 0.3))
        best_match = None
        best_distance = limit + 1

        for concept in concepts:
            distance = levenshtein_within(value, _german_label(concept).lower(), limit)
            if distance is not None and distance < best_distance:
                best_distance = distance
                best_match = concept
        return best_match

    def normalize_day_of_week(self, value: str) -> str:
        """Convert day name to short code (MO, TU, etc.)."""
        if not value:
            return value

        lower = value.lower().strip()
        return self.DAY_MAPPING.get(lower, value.upper() if len(value) == 2 else value)


def _german_label(concept: dict) -> str:
    label = concept.get("label", "")
    if isinstance(label, dict):
        label = label.get("de", label.get("en", ""))
    return label


def _concept_key(concept: dict, has_uris: bool, has_values: bool, fallback: Any) -> Any:
    """What a matched concept is written as: its URI, else its value, else the fallback."""
    if has_uris and concept.get("uri"):
        return concept.get("uri")
    if has_values and concept.get("value"):
        return concept.get("value")
    return fallback


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
            except Exception as e:
                logger.warning(
                    f"⚠️ Shape check without core.json ({context}/{version}): {e}"
                )
    except Exception as e:
        # No schema, no shape to check against; the write path decides on its own.
        logger.warning(f"⚠️ Shape check skipped, {schema_file} not loaded: {e}")
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
