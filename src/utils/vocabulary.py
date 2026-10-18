"""
What counts as a closed vocabulary, for every place that checks it (audit L-09).

The schemata know two closed kinds: "closed" (licences, languages, quality
scales) and "skos" (Fach, Bildungsstufe, Ressourcentyp - SKOS concept schemes
whose values are the concept URIs). Both allow only their concepts; "open" or
no type keeps free text.
"""

from typing import Any

CLOSED_VOCABULARY_TYPES = ("closed", "skos")


def is_closed_vocabulary(vocabulary: Any) -> bool:
    """
    Closed and with concepts to check against.

    A closed type without concepts (oeh:didactic_method is skos with an empty
    list) offers nothing to match: treated as open, or every answer would be
    refused and asked for again.
    """
    return (
        isinstance(vocabulary, dict)
        and vocabulary.get("type") in CLOSED_VOCABULARY_TYPES
        and bool(vocabulary.get("concepts"))
    )


def concept_keys(concepts: list) -> set:
    """What a value may be: a concept's 'uri', or its 'value' (languages, licences)."""
    keys = {c.get("uri") for c in concepts if c.get("uri")}
    keys.update(c.get("value") for c in concepts if c.get("value"))
    return keys
