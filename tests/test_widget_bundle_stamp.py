"""
The widget bundle in src/static/widget is one complete delivery of a build.

Audit 14.11., U-17/U-20: nothing tied the files here to a build of the web
component - a bundle edited by hand, copied in part or mixed from two builds
looked like any other. The widget's deploy script (build-elements.js) now
writes dist/build.json: its version, the build time, a SHA-256 per delivered
file and one over all of them. This recomputes it from what is in the folder.
"""

import hashlib
import json
from pathlib import Path

from src.config import Settings

WIDGET = Path(__file__).resolve().parents[1] / "src" / "static" / "widget"
STAMP = WIDGET / "dist" / "build.json"


def _delivered() -> dict[str, str]:
    """Path (as in build.json) -> SHA-256 of every file of a delivery."""
    found = {}
    for folder in ("dist", "assets/i18n"):
        for path in sorted((WIDGET / folder).rglob("*")):
            if path.is_file() and path != STAMP:
                name = path.relative_to(WIDGET).as_posix()
                found[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def test_the_bundle_is_the_delivery_its_stamp_describes():
    stamp = json.loads(STAMP.read_text(encoding="utf-8"))
    found = _delivered()

    changed = sorted(
        name
        for name in found.keys() | stamp["files"].keys()
        if found.get(name) != stamp["files"].get(name)
    )
    assert changed == [], f"changed, missing or extra files: {changed}"

    lines = "".join(f"{digest}  {name}\n" for name, digest in sorted(found.items()))
    assert hashlib.sha256(lines.encode()).hexdigest() == stamp["hash"]


def test_the_stamp_names_the_widget_and_its_version():
    stamp = json.loads(STAMP.read_text(encoding="utf-8"))

    assert stamp["name"] == "metadata-agent-canvas"
    # The agent serves a widget of its own major version (build-elements.js
    # refuses another one when it deploys).
    agent_version = Settings.model_fields["app_version"].default
    assert stamp["version"].split(".")[0] == agent_version.split(".")[0]
    assert stamp["builtAt"].endswith("Z")
