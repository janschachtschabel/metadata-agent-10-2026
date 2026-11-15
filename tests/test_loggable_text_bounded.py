"""
loggable_text is linear and its output bounded; request URLs have a length
(audit 14.11., S-31).

A browser error that names a URL with a quote in its path and a long rest took
quadratic time in the URL pattern: 40,000 letters held the event loop for 26 s.
The scheme of the pattern is bounded now, the text is cut before the search,
and `url`, `preview_url` and `source_url` take at most 8192 characters.
"""

import time

import pytest
from pydantic import ValidationError

from src.models.schemas import (
    DetectContentTypeRequest,
    ExtractFieldRequest,
    GenerateRequest,
    ScreenshotRequest,
    UploadRequest,
)
from src.utils.loggable import MAX_LOGGABLE_TEXT, loggable_text


def _browser_error(url: str) -> str:
    return (
        f"Page.goto: net::ERR_CONNECTION_REFUSED at {url}\n"
        f'Call log:\n  - navigating to "{url}", waiting until "domcontentloaded"\n'
    )


def test_letters_after_a_quote_cost_no_time():
    text = _browser_error("http://192.0.2.1:81/'" + "a" * 150_000)

    start = time.perf_counter()
    line = loggable_text(text)
    seconds = time.perf_counter() - start

    assert seconds < 0.1
    assert len(line) <= MAX_LOGGABLE_TEXT + 1


def test_a_long_run_of_scheme_characters_costs_no_time():
    start = time.perf_counter()
    loggable_text("a" * MAX_LOGGABLE_TEXT)

    assert time.perf_counter() - start < 0.1


def test_urls_in_a_message_still_lose_query_and_credentials():
    line = loggable_text(
        "net::ERR_FAILED at https://user:geheim@example.org/pfad?token=abc\nCall log"
    )

    assert line == "net::ERR_FAILED at https://example.org/pfad Call log"


def test_a_long_message_is_cut():
    line = loggable_text("x" * (MAX_LOGGABLE_TEXT + 500))

    assert line == "x" * MAX_LOGGABLE_TEXT + "…"


def test_a_url_cut_inside_its_credentials_shows_none_of_them():
    start = "https://user:"
    text = "x" * (MAX_LOGGABLE_TEXT - len(start) - 3) + start + "geheim@example.org/"

    line = loggable_text(text)

    assert "geh" not in line
    assert line.endswith("https://…")


URL = "https://example.org/" + "a" * 8192


@pytest.mark.parametrize(
    "model, body",
    [
        (ScreenshotRequest, {"url": URL}),
        (GenerateRequest, {"text": "x", "preview_url": URL}),
        (GenerateRequest, {"input_source": "url", "source_url": URL}),
        (DetectContentTypeRequest, {"input_source": "url", "source_url": URL}),
        (
            ExtractFieldRequest,
            {"source_url": URL, "schema_file": "core.json", "field_id": "cclom:title"},
        ),
        (UploadRequest, {"metadata": {"cclom:title": "T"}, "preview_url": URL}),
    ],
)
def test_a_url_over_8192_characters_is_refused(model, body):
    with pytest.raises(ValidationError, match="at most 8192"):
        model(**body)

    field = next(name for name, value in body.items() if value == URL)
    assert model(**{**body, field: URL[:8192]})
