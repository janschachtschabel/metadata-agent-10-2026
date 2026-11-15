"""
Report the Chromium that playwright installs, and fail when it is too old.

The screenshot browser renders arbitrary public pages inside the pod. playwright
pins its version, and no image scanner sees it (audit 18.10. final). Run in CI
after `playwright install chromium`:

    uv run python scripts/check_browser_age.py

Exit 1 when the bundled Chromium is more than MAX_MAJORS_BEHIND major versions
behind the current stable Chrome; then raise playwright (pyproject.toml, uv
lock, requirements.txt).

When the version service cannot be reached, it only reports the bundled
version - unless REQUIRE_BROWSER_AGE=1 is set (both CIs): then the upload time
of the locked playwright in uv.lock stands in, and the check fails when that is
more than MAX_LOCK_AGE_DAYS ago or cannot be read either (audit 14.11., O-32:
a check that passes whenever it cannot check reports nothing).
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MAX_MAJORS_BEHIND = 2
# Chrome gets a new major about every four weeks, and playwright ships the one
# ahead of stable: after 90 days its Chromium is about two majors behind.
MAX_LOCK_AGE_DAYS = 90

STABLE_URL = (
    "https://versionhistory.googleapis.com/v1/chrome/platforms/linux/channels/"
    "stable/versions?pageSize=1"
)
LOCK = Path(__file__).resolve().parents[1] / "uv.lock"


def bundled_chromium_version() -> str:
    """The Chromium version of the installed playwright (its browsers.json)."""
    import playwright

    browsers = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
    data = json.loads(browsers.read_text(encoding="utf-8"))
    return next(
        b["browserVersion"] for b in data["browsers"] if b["name"] == "chromium"
    )


def stable_chrome_version() -> str:
    """The newest stable Chrome for Linux, from Google's version history API."""
    with urllib.request.urlopen(STABLE_URL, timeout=10) as response:
        return json.loads(response.read())["versions"][0]["version"]


def _major(version: str) -> int:
    return int(version.split(".", 1)[0])


def recent_enough(bundled: str, stable: str) -> bool:
    return _major(stable) - _major(bundled) <= MAX_MAJORS_BEHIND


def playwright_upload_time(lock: Path = LOCK) -> datetime:
    """
    When the locked playwright was uploaded to the package index (uv.lock).

    Raises:
        OSError: the lock file cannot be read.
        ValueError: it names no upload time for playwright.
    """
    entry = re.search(
        r'^name = "playwright"\n(.*?)(?=^\[\[package\]\]|\Z)',
        lock.read_text(encoding="utf-8"),
        re.DOTALL | re.MULTILINE,
    )
    times = re.findall(r'upload-time = "([^"]+)"', entry.group(1) if entry else "")
    if not times:
        raise ValueError(f"no upload time for playwright in {lock.name}")
    return min(datetime.fromisoformat(time) for time in times)


def lock_age_days(lock: Path = LOCK, now: Optional[datetime] = None) -> int:
    """Whole days since the locked playwright was uploaded."""
    now = now or datetime.now(timezone.utc)
    return (now - playwright_upload_time(lock)).days


def _judge_by_the_lock(required: bool) -> int:
    """The offline measure: the browser is as old as the playwright that ships it."""
    try:
        age = lock_age_days()
    except (OSError, ValueError) as e:
        print(f"Upload time of playwright in uv.lock unknown ({type(e).__name__}).")
        if required:
            print("REQUIRE_BROWSER_AGE=1: the age of the browser must be known.")
        return 1 if required else 0
    print(f"playwright in uv.lock was uploaded {age} days ago.")
    if age <= MAX_LOCK_AGE_DAYS:
        return 0
    print(
        f"More than {MAX_LOCK_AGE_DAYS} days: raise playwright (pyproject.toml, "
        "uv lock, requirements.txt)."
    )
    return 1 if required else 0


def main() -> int:
    required = os.environ.get("REQUIRE_BROWSER_AGE") == "1"
    bundled = bundled_chromium_version()
    print(f"Chromium of playwright: {bundled}")
    try:
        stable = stable_chrome_version()
    except (OSError, ValueError, KeyError, IndexError) as e:
        print(f"Current stable Chrome unknown ({type(e).__name__}).")
        return _judge_by_the_lock(required)
    print(f"Current stable Chrome: {stable}")
    if recent_enough(bundled, stable):
        return 0
    print(
        f"Chromium {bundled} is more than {MAX_MAJORS_BEHIND} major versions behind "
        f"{stable}: raise playwright (pyproject.toml, uv lock, requirements.txt)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
