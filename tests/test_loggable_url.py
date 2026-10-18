"""
URLs in log lines (audit 18.10. final, S-17).

Callers' URLs went into logs whole: query strings carry tokens, user info
passwords, and a line break inside the URL forged a second log line with
LOG_FORMAT=text.
"""

import pytest

from src.utils.loggable import loggable_text, loggable_url


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://example.org/a/b?token=secret#frag", "https://example.org/a/b"),
        ("https://user:pw@example.org:8443/x", "https://example.org:8443/x"),
        ("http://example.org", "http://example.org"),
    ],
)
def test_only_scheme_host_and_path_are_logged(url, expected):
    assert loggable_url(url) == expected


@pytest.mark.parametrize("brk", ["\n", "\r", "\r\n"])
def test_a_line_break_cannot_forge_a_second_log_line(brk):
    logged = loggable_url(f"https://example.org/a{brk}2026-10-18 ERROR forged")

    assert "\n" not in logged and "\r" not in logged


@pytest.mark.parametrize("value", ["", "not a url", "http://[::1", None, 42])
def test_garbage_is_logged_safely(value):
    logged = loggable_url(value)

    assert isinstance(logged, str) and "\n" not in logged


def test_urls_inside_a_message_lose_query_and_credentials():
    """Browser errors name the URL they failed on - with its query string."""
    message = (
        "Page.goto: net::ERR_CONNECTION_REFUSED at https://u:pw@example.org/a?token=geheim\n"
        'Call log:\n  - navigating to "https://example.org/a?token=geheim#x", waiting'
    )

    logged = loggable_text(message)

    assert "\n" not in logged
    assert "https://example.org/a" in logged and "ERR_CONNECTION_REFUSED" in logged
    for secret in ("geheim", "token", "pw@", "#x"):
        assert secret not in logged, secret


def test_a_message_without_urls_stays_as_it_is():
    assert loggable_text("Timeout 30000ms exceeded.") == "Timeout 30000ms exceeded."
