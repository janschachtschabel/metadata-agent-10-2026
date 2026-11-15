"""
The Chromium of the screenshots must not grow old unseen (audit 18.10. final).

playwright pins the browser it downloads; no image scanner sees it. The CI runs
scripts/check_browser_age.py: it reports the bundled version and fails when it
is more than MAX_MAJORS_BEHIND major versions behind the current stable Chrome.

Audit 14.11., O-32: when the version service could not be reached, the check
passed without having checked. With REQUIRE_BROWSER_AGE=1 (both CIs) it then
judges by the upload time of the locked playwright in uv.lock, and fails when
that is unknown too.
"""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
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


def _offline(check, monkeypatch, lock_age=None):
    def offline():
        raise OSError("no network")

    def age():
        if isinstance(lock_age, Exception):
            raise lock_age
        return lock_age

    monkeypatch.setattr(check, "stable_chrome_version", offline)
    if lock_age is not None:
        monkeypatch.setattr(check, "lock_age_days", age)


def test_an_unreachable_version_service_only_reports(check, monkeypatch, capsys):
    monkeypatch.delenv("REQUIRE_BROWSER_AGE", raising=False)
    _offline(check, monkeypatch)

    assert check.main() == 0
    assert "Chromium" in capsys.readouterr().out


@pytest.mark.parametrize("lock_age", [400, ValueError("no upload time")])
def test_without_the_switch_an_old_or_unknown_lock_still_only_reports(
    check, monkeypatch, lock_age
):
    monkeypatch.delenv("REQUIRE_BROWSER_AGE", raising=False)
    _offline(check, monkeypatch, lock_age)

    assert check.main() == 0


def test_where_the_age_is_required_a_recent_lock_stands_in(check, monkeypatch, capsys):
    monkeypatch.setenv("REQUIRE_BROWSER_AGE", "1")
    _offline(check, monkeypatch, lock_age=check.MAX_LOCK_AGE_DAYS)

    assert check.main() == 0
    assert f"{check.MAX_LOCK_AGE_DAYS} days ago" in capsys.readouterr().out


def test_where_the_age_is_required_an_old_lock_fails(check, monkeypatch, capsys):
    monkeypatch.setenv("REQUIRE_BROWSER_AGE", "1")
    _offline(check, monkeypatch, lock_age=check.MAX_LOCK_AGE_DAYS + 1)

    assert check.main() == 1
    assert "raise playwright" in capsys.readouterr().out


@pytest.mark.parametrize("error", [OSError("no uv.lock"), ValueError("no upload time")])
def test_where_the_age_is_required_not_knowing_it_fails(check, monkeypatch, error):
    monkeypatch.setenv("REQUIRE_BROWSER_AGE", "1")
    _offline(check, monkeypatch, lock_age=error)

    assert check.main() == 1


def test_a_reachable_version_service_decides_also_where_the_age_is_required(
    check, monkeypatch
):
    monkeypatch.setenv("REQUIRE_BROWSER_AGE", "1")
    monkeypatch.setattr(check, "bundled_chromium_version", lambda: "153.0.1")
    monkeypatch.setattr(check, "stable_chrome_version", lambda: "154.0.1")
    monkeypatch.setattr(check, "lock_age_days", lambda: 10_000)

    assert check.main() == 0


LOCK = """\
[[package]]
name = "pyee"
version = "13.0.1"
wheels = [
    { url = "https://example/pyee.whl", upload-time = "2020-01-01T00:00:00Z" },
]

[[package]]
name = "playwright"
version = "1.63.0"
wheels = [
    { url = "https://example/a.whl", hash = "sha256:aa", size = 1, upload-time = "2026-09-15T16:49:06.045Z" },
    { url = "https://example/b.whl", hash = "sha256:bb", size = 1, upload-time = "2026-09-15T16:49:01Z" },
]

[[package]]
name = "playwright-extra"
version = "1.0"
wheels = [
    { url = "https://example/c.whl", upload-time = "2030-01-01T00:00:00Z" },
]
"""


def test_the_upload_time_is_read_from_the_playwright_entry_of_the_lock(check, tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK, encoding="utf-8")

    uploaded = check.playwright_upload_time(lock)

    assert uploaded == datetime(2026, 9, 15, 16, 49, 1, tzinfo=timezone.utc)
    now = uploaded + timedelta(days=60, hours=5)
    assert check.lock_age_days(lock, now=now) == 60


def test_a_lock_without_upload_times_is_unknown_not_recent(check, tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text('[[package]]\nname = "playwright"\nversion = "1"\n', "utf-8")

    with pytest.raises(ValueError):
        check.playwright_upload_time(lock)


def test_the_lock_of_the_project_names_the_upload_time(check):
    assert check.playwright_upload_time().year >= 2026


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
