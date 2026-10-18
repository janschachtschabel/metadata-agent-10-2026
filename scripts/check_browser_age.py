"""
Report the Chromium that playwright installs, and fail when it is too old.

The screenshot browser renders arbitrary public pages inside the pod. playwright
pins its version, and no image scanner sees it (audit 18.10. final). Run in CI
after `playwright install chromium`:

    uv run python scripts/check_browser_age.py

Exit 1 when the bundled Chromium is more than MAX_MAJORS_BEHIND major versions
behind the current stable Chrome; then raise playwright (pyproject.toml, uv
lock, requirements.txt). When the version service cannot be reached, it only
reports the bundled version.
"""

import json
import sys
import urllib.request
from pathlib import Path

MAX_MAJORS_BEHIND = 2

STABLE_URL = (
    "https://versionhistory.googleapis.com/v1/chrome/platforms/linux/channels/"
    "stable/versions?pageSize=1"
)


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


def main() -> int:
    bundled = bundled_chromium_version()
    print(f"Chromium of playwright: {bundled}")
    try:
        stable = stable_chrome_version()
    except (OSError, ValueError, KeyError, IndexError) as e:
        print(f"Current stable Chrome unknown ({type(e).__name__}) - age not checked.")
        return 0
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
