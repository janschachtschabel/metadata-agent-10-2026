"""
Matching a value to the concepts of a vocabulary: the one place that does it
(audit 14.11.2026, L-34 / M-04).

Until then the model answer path, FieldNormalizer and /validate each matched
on their own and disagreed: "Deutsh" became the label "Deutsch" on one path
and `de` on the other, "German" was refused from the model and accepted from
a caller, and /validate knew no suggestion for vocabularies without URIs.

A concept is written as its key: the URI, else the `value`. (The frozen 1.8.0
schemas have closed vocabularies whose concepts carry neither; their key is
the German label.) No function here returns a label for a concept that has a
URI or a value.

- match_model_answer: what a model answered. Its key (a `value` in any case),
  a label or altLabel in any language, and - closed vocabularies only, answers
  of MIN_FUZZY_LENGTH characters and more - the nearest German label within
  30 % of the length and three edits. Without a concept: None in a closed
  vocabulary, the answer itself in an open one.
- match_caller_value: what a caller or the node brings. Its key, or a label
  or altLabel in any language. Without a concept the value stays as it is - a
  legacy concept the repository still holds is no reason to drop it - and
  nothing is guessed.
- is_concept and suggest_concept: for /validate - is this value a key, and
  which key was probably meant.

Numbers and booleans are compared by their string form (3 is "3", True is
"true"); 0 and False are values like any other. Only text is matched by
labels or by distance.
"""

from typing import Any, Optional

from ..utils.text_utils import levenshtein_within
from ..utils.vocabulary import is_closed_vocabulary

# Shorter answers are a concept or nothing: one edit turns "7" into "0" and
# "14" into "12" - an invented age rating (audit 14.11., L-32).
MIN_FUZZY_LENGTH = 4

_NO_MATCH: Any = object()


class _Index:
    """The lookups of one concept list, built once."""

    def __init__(self, concepts: list):
        self.size = len(concepts)
        # URI or value as written -> key
        self.own: dict[str, Any] = {}
        # value in lower case -> key
        self.values: dict[str, Any] = {}
        # label or altLabel of any language, lower case -> key
        self.labels: dict[str, Any] = {}
        # (German label in lower case, key), in the order of the vocabulary
        self.german: list[tuple[str, Any]] = []
        # (URI in lower case without trailing slash, URI)
        self.uris: list[tuple[str, str]] = []
        for concept in concepts:
            if isinstance(concept, dict):
                self._add(concept)

    def _add(self, concept: dict) -> None:
        uri, value = concept.get("uri"), concept.get("value")
        german = german_label(concept)
        if uri:
            key = uri
        elif value is not None and value != "":
            key = value
        else:
            key = german
        if key is None or key == "":
            return
        for own in (uri, value, key):
            if own is not None and own != "":
                self.own.setdefault(str(own), key)
        if value is not None and value != "":
            self.values.setdefault(str(value).lower(), key)
        if isinstance(uri, str) and uri:
            self.uris.append((uri.lower().rstrip("/"), uri))
        label = concept.get("label", "")
        names = list(label.values()) if isinstance(label, dict) else [label]
        for name in [*names, *(concept.get("altLabels") or [])]:
            if isinstance(name, str) and name.strip():
                self.labels.setdefault(name.strip().lower(), key)
        if german:
            self.german.append((german.lower(), key))


# id(concepts) -> (concepts, index). The schema loader hands out the same list
# for as long as it caches the schema; keeping the list here keeps its id from
# being reused. Bounded for lists built per call (tests, ad-hoc vocabularies).
_INDEXES: dict[int, tuple[list, _Index]] = {}
_MAX_INDEXES = 512


def _index(concepts: list) -> _Index:
    cached = _INDEXES.get(id(concepts))
    if cached is not None and cached[0] is concepts and cached[1].size == len(concepts):
        return cached[1]
    index = _Index(concepts)
    if len(_INDEXES) >= _MAX_INDEXES:
        _INDEXES.pop(next(iter(_INDEXES)))
    _INDEXES[id(concepts)] = (concepts, index)
    return index


def german_label(concept: dict) -> str:
    """The German label of a concept, else the English one, else ""."""
    label = concept.get("label", "")
    if isinstance(label, dict):
        label = label.get("de", label.get("en", ""))
    return label if isinstance(label, str) else ""


def _concepts(vocabulary: Any) -> list:
    concepts = vocabulary.get("concepts") if isinstance(vocabulary, dict) else None
    return concepts if isinstance(concepts, list) else []


def _is_number(value: Any) -> bool:
    return isinstance(value, (bool, int, float))


def _exact(value: Any, index: _Index, any_case: bool) -> Any:
    """
    The key a value names exactly, else _NO_MATCH: by its key as written, by
    a `value` in another case (`any_case`), or by a label.
    """
    numeric = _is_number(value)
    if numeric:
        value = str(value).lower()  # str(True) is "True", the concept "true"
    elif not isinstance(value, str):
        return _NO_MATCH  # an object or a list is no concept
    text = value.strip()
    for candidate in (value, text):
        if candidate in index.own:
            return index.own[candidate]
    lowered = text.lower()
    if (any_case or numeric) and lowered in index.values:
        return index.values[lowered]
    return index.labels.get(lowered, _NO_MATCH)


def nearest_label(text: str, labels: list[tuple[str, Any]]) -> Any:
    """
    The key of the label closest to `text` (lower case), within 30 % of its
    length and at most three edits; _NO_MATCH for a short text or none close.
    """
    if len(text) < MIN_FUZZY_LENGTH:
        return _NO_MATCH
    limit = min(3, int(len(text) * 0.3))
    best = _NO_MATCH
    best_distance = limit + 1
    for label, key in labels:
        distance = levenshtein_within(text, label, limit)
        if distance is not None and distance < best_distance:
            best_distance = distance
            best = key
    return best


def match_model_answer(value: Any, vocabulary: Any) -> Any:
    """
    The concept key a model answer stands for.

    None when a closed vocabulary has no such concept; an open vocabulary (or
    one without concepts) keeps the answer.
    """
    concepts = _concepts(vocabulary)
    if not concepts:
        return value
    index = _index(concepts)
    key = _exact(value, index, any_case=True)
    if key is not _NO_MATCH:
        return key
    if not is_closed_vocabulary(vocabulary):
        return value
    if isinstance(value, str):
        key = nearest_label(value.lower().strip(), index.german)
        if key is not _NO_MATCH:
            return key
    return None


def match_caller_value(value: Any, vocabulary: Any) -> Any:
    """
    The concept key a caller's value names exactly (as 2.1.1 matched: key, or
    label / altLabel in any language), else the value itself.
    """
    concepts = _concepts(vocabulary)
    if not concepts:
        return value
    key = _exact(value, _index(concepts), any_case=False)
    return value if key is _NO_MATCH else key


def is_concept(value: Any, concepts: list) -> bool:
    """
    Whether a value is a key of one of the concepts.

    Text must be the key as the vocabulary spells it; a number or boolean
    counts by its string form (3 for "3", True for "true", 0 for "0").
    """
    index = _index(concepts)
    if _is_number(value):
        return str(value).lower() in index.values
    return isinstance(value, str) and value in index.own


def is_valid_answer(value: Any, vocabulary: Any) -> bool:
    """
    A normalised model answer holds a concept of the closed vocabulary.

    A list counts when at least one entry is a concept; extract_field keeps
    those and drops the rest. Asking again for a partial match threw its
    matches away whenever a later attempt matched less (final audit 18.10.).
    A list matching nothing is asked again (audit L-07); an empty list is "no
    value", nothing to ask again about.
    """
    if value is None:
        return False
    concepts = _concepts(vocabulary)
    if isinstance(value, list):
        return not value or any(is_concept(entry, concepts) for entry in value)
    return is_concept(value, concepts)


def suggest_concept(value: Any, concepts: list) -> Optional[Any]:
    """
    The key that was probably meant by a value which is no concept, or None.

    In this order: the concept the value names by `value` in another case or
    by a label; the concept whose URI ends in the value ("460",
    "discipline/460" - whole trailing path segments only); the concept with
    the nearest label in any language (text of MIN_FUZZY_LENGTH characters
    and more). Also for vocabularies keyed by `value`: "Deutsh" gives "de".
    A number is looked up by its string form and has no nearest concept.
    """
    index = _index(concepts)
    if is_concept(value, concepts):
        return None
    key = _exact(value, index, any_case=True)
    if key is _NO_MATCH and isinstance(value, str) and value.strip().strip("/"):
        lowered = value.strip().lower()
        tail = "/" + lowered.strip("/")
        key = next((uri for end, uri in index.uris if end.endswith(tail)), _NO_MATCH)
        if key is _NO_MATCH:
            key = nearest_label(lowered, list(index.labels.items()))
    return None if key is _NO_MATCH else key
