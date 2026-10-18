"""REQUIRE_BROWSER=1 turns a skipped browser test into a failure (audit T-03)."""

import pytest
from browser_requirement import browser_missing


def test_without_the_requirement_a_missing_browser_skips(monkeypatch):
    monkeypatch.delenv("REQUIRE_BROWSER", raising=False)

    with pytest.raises(pytest.skip.Exception):
        browser_missing("Chromium missing")


def test_with_the_requirement_a_missing_browser_fails(monkeypatch):
    monkeypatch.setenv("REQUIRE_BROWSER", "1")

    with pytest.raises(pytest.fail.Exception, match="REQUIRE_BROWSER"):
        browser_missing("Chromium missing")


@pytest.mark.parametrize(
    "name", ["test_screenshot_browser_policy.py", "test_widget_bundle_browser.py"]
)
def test_the_browser_tests_use_it(name):
    from pathlib import Path

    source = (Path(__file__).parent / name).read_text("utf-8")

    assert "browser_missing(" in source
    assert "pytest.skip(" not in source
