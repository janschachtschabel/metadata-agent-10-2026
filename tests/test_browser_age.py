"""
The Chromium of the screenshots must not grow old unseen (audit 18.10. final).

playwright pins the browser it downloads; no image scanner sees it. The CI runs
scripts/check_browser_age.py: it reports the bundled version and fails when it
is more than MAX_MAJORS_BEHIND major versions behind the current stable Chrome.
"""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_browser_age.py"


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("check_browser_age", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_bundled_version_is_read_from_playwright(check):
    version = check.bundled_chromium_version()

    assert int(version.split(".")[0]) >= 145


@pytest.mark.parametrize(
    "bundled, stable, ok",
    [
        ("153.0.8010.12", "153.0.1", True),
        ("151.0.1", "153.0.1", True),
        ("150.0.1", "153.0.1", False),
    ],
)
def test_more_than_two_majors_behind_fails(check, bundled, stable, ok):
    assert check.MAX_MAJORS_BEHIND == 2
    assert check.recent_enough(bundled, stable) is ok


def test_an_unreachable_version_service_only_reports(check, monkeypatch, capsys):
    def offline():
        raise OSError("no network")

    monkeypatch.setattr(check, "stable_chrome_version", offline)

    assert check.main() == 0
    assert "Chromium" in capsys.readouterr().out


def test_a_stale_browser_fails_the_job(check, monkeypatch):
    monkeypatch.setattr(check, "bundled_chromium_version", lambda: "140.0.1")
    monkeypatch.setattr(check, "stable_chrome_version", lambda: "153.0.1")

    assert check.main() == 1


def test_the_stable_version_is_read_from_the_answer(check, monkeypatch):
    answer = json.dumps({"versions": [{"version": "153.0.8010.70"}]}).encode()

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return answer

    monkeypatch.setattr(check.urllib.request, "urlopen", lambda *a, **kw: _Response())

    assert check.stable_chrome_version() == "153.0.8010.70"
