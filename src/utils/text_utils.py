"""Shared text utility functions."""

from typing import Optional


def levenshtein_distance(a: str, b: str) -> int:
    """Calculate Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return levenshtein_distance(b, a)

    if len(b) == 0:
        return len(a)

    previous_row = list(range(len(b) + 1))
    for i, c1 in enumerate(a):
        current_row = [i + 1]
        for j, c2 in enumerate(b):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def levenshtein_within(a: str, b: str, limit: int) -> Optional[int]:
    """
    The edit distance of a and b if it is at most `limit`, else None.

    Fuzzy vocabulary matching only accepts distances of a few edits, but
    compared every value with every label in full - 220 labels of ccm:oeh_lrt
    per field, on the event loop (audit P-05). Here a length difference above
    the limit ends it at once, only cells within `limit` of the diagonal are
    computed, and a row without any cell inside the limit ends it early.
    """
    if abs(len(a) - len(b)) > limit:
        return None
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a) if len(a) <= limit else None

    beyond = limit + 1
    previous = [j if j <= limit else beyond for j in range(len(b) + 1)]
    for i in range(1, len(a) + 1):
        current = [i if i <= limit else beyond] + [beyond] * len(b)
        low, high = max(1, i - limit), min(len(b), i + limit)
        for j in range(low, high + 1):
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (a[i - 1] != b[j - 1]),
                beyond,
            )
        if min(current[max(0, low - 1) : high + 1]) > limit:
            return None
        previous = current
    return previous[-1] if previous[-1] <= limit else None
