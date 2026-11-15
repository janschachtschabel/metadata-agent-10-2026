"""
Field Normalizer Service for metadata API.
Normalizes user input and extracted values based on field schema.
Ported from metadata-agent-canvas-optimized FieldNormalizerService.
"""

from typing import Any, Optional

from ..utils.dates_numbers import (
    ImpossibleDate,
    read_date,
    read_datetime,
    read_time,
    to_number,
)
from ..utils.scalar_values import normalize_boolean, normalize_url
from ..utils.vocabulary import is_closed_vocabulary
from .object_shapes import is_object_field, object_key
from .vocabulary_matching import match_caller_value, match_model_answer

# Defined here before 14.11.; the write path imports it from object_shapes.
from .object_shapes import (
    find_object_shape_violations as find_object_shape_violations,
)


class FieldNormalizer:
    """Normalizes field values based on schema definitions."""

    def normalize_field_value(
        self,
        value: Any,
        field_schema: dict,
        normalize_vocabularies: bool = True,
        exact_only: bool = False,
        notes: Optional[list[str]] = None,
    ) -> Any:
        """
        Normalize a single field value based on its schema.

        The shape follows the schema: a scalar on a list field becomes a list
        of one entry; a list on a single-value field gives its first entry
        (audit 14.11., L-45).

        Args:
            value: The value to normalize
            field_schema: The field's schema definition
            normalize_vocabularies: Whether to normalize vocabulary values
            exact_only: For values the caller or the node brings: a vocabulary
                value is matched only exactly (URI, value, label or altLabel
                in any language) and kept unchanged when nothing matches
            notes: Receives a line when entries of a list were left out
                because the field takes one value

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

        # The schema says list: a scalar is its one entry. It used to stay a
        # scalar, and the caller got a string where a list is declared.
        if is_multiple and not isinstance(value, list):
            value = [value]

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
                if len(value) > 1 and notes is not None:
                    notes.append(
                        f"Feld nimmt einen Wert auf: {len(value) - 1} weitere "
                        "Wert(e) nicht übernommen."
                    )
                value = value[0]
                if value is None or value == "":
                    return value

        return self._normalize_single_value(
            value, datatype, vocabulary, normalize_vocabularies, exact_only
        )

    def _is_object_field(self, field_schema: dict) -> bool:
        """Does the schema say this field holds objects (object_shapes)?"""
        return is_object_field(field_schema)

    def _object_key(self, field_schema: dict) -> str:
        """The sub-field a bare string belongs in (object_shapes)."""
        return object_key(field_schema)

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

        # The rules for both kinds of value: services/vocabulary_matching.py.
        if vocabulary and normalize_vocabularies:
            # The caller's own values: exact matches only, unknown ones stay
            # (final audit 18.10.), and no fuzzy search over unbounded lists.
            if exact_only and vocabulary.get("concepts"):
                return match_caller_value(value, vocabulary)
            normalized = match_model_answer(value, vocabulary)
            # A closed vocabulary allows only its concepts (audit L-08).
            if normalized is not None or is_closed_vocabulary(vocabulary):
                return normalized

        # Type-specific normalization
        if datatype == "boolean":
            return normalize_boolean(value)
        elif datatype in ("number", "integer"):
            return self._normalize_number(value, datatype == "integer")
        elif datatype == "date":
            return self._normalize_date(value)
        elif datatype == "datetime":
            return self._normalize_datetime(value)
        elif datatype == "time":
            return self._normalize_time(value)
        elif datatype == "uri":
            return normalize_url(value)

        # Default: return as-is (strings)
        return value

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


# Singleton instance
_normalizer: Optional[FieldNormalizer] = None


def get_field_normalizer() -> FieldNormalizer:
    """Get singleton instance of field normalizer."""
    global _normalizer
    if _normalizer is None:
        _normalizer = FieldNormalizer()
    return _normalizer
