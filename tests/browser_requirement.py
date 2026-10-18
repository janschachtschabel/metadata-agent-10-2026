"""
Browser tests skip without Chromium or the widget bundle - locally fine, in CI
a silent hole (audit T-03): a broken `playwright install` turned the URL policy
and widget tests into green skips. Both CI test jobs set REQUIRE_BROWSER=1, and
then a missing browser fails the run.
"""

import os

import pytest


def browser_missing(reason: str) -> None:
    """Skip - or fail when the run requires the browser tests."""
    if os.environ.get("REQUIRE_BROWSER") == "1":
        pytest.fail(f"{reason} (REQUIRE_BROWSER=1)", pytrace=False)
    pytest.skip(reason)
