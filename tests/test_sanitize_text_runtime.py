"""
Cleaning the `text` of a request costs what the kept part costs (audit
14.11.2026, L-41).

sanitize_text ran its passes over the whole text before the API layer cut it
to 150,000 characters: 406 ms for a body of 19 MB, on the event loop. Now the
passes read a multiple of what is kept; the rest stays as it came, so the
warning about the cut still names the real length.
"""

import time

import pytest

from src.api.input_resolver import MAX_TEXT_LENGTH, truncate, truncation_warnings
from src.models import common, schemas
from src.models.schemas import GenerateRequest, sanitize_text

LIMIT = schemas.SANITIZED_LENGTH
# 19 MB as the auditor measured it; line breaks and blanks as a page has them.
HUGE = ("Ein Satz mit  Leerraum.\r\n\r\n\r\n\x00" * 730_000)[:19_000_000]


def _best_of(runs, function, *args):
    best = float("inf")
    for _ in range(runs):
        start = time.perf_counter()
        function(*args)
        best = min(best, time.perf_counter() - start)
    return best


@pytest.mark.parametrize(
    "text, cleaned",
    [
        ("", ""),
        ("  Ein Text.  ", "Ein Text."),
        ("a\x00b\x07c\x7fd", "abcd"),
        ("Tab\tbleibt\nZeile", "Tab\tbleibt\nZeile"),
        ("a\u00a0b\u2003c\u202fd\u3000e", "a b c d e"),
        ("eins\r\nzwei\rdrei", "eins\nzwei\ndrei"),
        ("eins\n\n\n\n\nzwei", "eins\n\nzwei"),
        ("eins\r\n\r\n\r\nzwei\n", "eins\n\nzwei"),
    ],
)
def test_a_text_under_the_limit_is_cleaned_as_before(text, cleaned):
    assert sanitize_text(text) == cleaned


def test_a_text_of_exactly_the_limit_is_cleaned_to_its_end():
    text = "a" * (LIMIT - 4) + "\x00b\r\n"

    assert sanitize_text(text) == "a" * (LIMIT - 4) + "b"


def test_the_limit_is_a_multiple_of_what_the_api_keeps():
    assert schemas.SANITIZED_LENGTH % MAX_TEXT_LENGTH == 0
    assert schemas.SANITIZED_LENGTH >= 2 * MAX_TEXT_LENGTH


def test_the_passes_read_no_more_than_the_limit(monkeypatch):
    seen = []
    real_sub = common.re.sub

    def recording(pattern, replacement, string, *args, **kwargs):
        seen.append(len(string))
        return real_sub(pattern, replacement, string, *args, **kwargs)

    monkeypatch.setattr(common.re, "sub", recording)

    sanitize_text(HUGE)

    assert seen and max(seen) <= LIMIT


def test_19_mb_cost_little_more_than_the_part_that_is_cleaned():
    part = _best_of(5, sanitize_text, HUGE[:LIMIT])

    whole = _best_of(3, sanitize_text, HUGE)

    # Measured: the same order (two copies of the rest on top); the passes
    # over all of it took 32 times the part.
    assert whole < 8 * part + 0.05


def test_what_the_api_keeps_of_a_long_text_is_clean():
    request = GenerateRequest(text=HUGE)

    kept = truncate(request.text)

    assert len(kept) == MAX_TEXT_LENGTH
    assert kept == sanitize_text(HUGE[:LIMIT])[:MAX_TEXT_LENGTH]
    assert "\r" not in kept and "\x00" not in kept and "\n\n\n" not in kept


def test_the_cut_warning_still_names_the_length_of_a_long_text():
    text = "Wort " * 199_999 + "Wort."  # 1,000,000 characters

    request = GenerateRequest(text=text)

    assert truncation_warnings(request.text, truncate(request.text)) == [
        "Quelltext auf 150.000 Zeichen gekürzt (Original: 1.000.000 Zeichen)."
    ]
