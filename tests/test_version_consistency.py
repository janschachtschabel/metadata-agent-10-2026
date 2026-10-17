"""
One version, written in four places.

pyproject.toml is the source; uv.lock repeats it (`uv run --locked` fails
when it does not), the settings default is what /health and the spec report,
and the Helm chart deploys it (tests/test_helm_chart.py). A bump that misses
one of them ships an image that names the wrong version.
"""

import re
from pathlib import Path

from src.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def _project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text("utf-8")
    return re.search(r'^version = "([^"]+)"', text, re.MULTILINE).group(1)


def test_the_lock_file_names_the_project_version():
    lock = (ROOT / "uv.lock").read_text("utf-8")
    entry = re.search(r'name = "metadata-agent-api"\nversion = "([^"]+)"', lock).group(
        1
    )

    assert entry == _project_version()


def test_health_and_spec_report_the_project_version():
    assert Settings(_env_file=None).app_version == _project_version()
