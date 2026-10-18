"""
License of the service and notices of what its image ships (audit DEP-04).

- LICENSE is the unchanged Apache License 2.0 (the checksum of the text at
  apache.org/licenses/LICENSE-2.0.txt), with no copyright holder filled in.
- NOTICE names every Python package the image installs (requirements.txt),
  the browser and the fonts; the image carries both files.
"""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APACHE_2_0_MD5 = "3b83ef96387f14655fc854ddc3c6bd57"


def test_the_license_is_the_unchanged_apache_2_0_text():
    text = (ROOT / "LICENSE").read_bytes().replace(b"\r\n", b"\n")

    assert hashlib.md5(text).hexdigest() == APACHE_2_0_MD5  # noqa: S324 - identity, not security
    assert b"Copyright [yyyy] [name of copyright owner]" in text


def test_the_project_declares_the_license():
    assert re.search(
        r'^license = "Apache-2.0"$',
        (ROOT / "pyproject.toml").read_text("utf-8"),
        re.MULTILINE,
    )


def test_the_notice_names_every_package_the_image_installs():
    notice = (ROOT / "NOTICE").read_text("utf-8")
    pinned = re.findall(
        r"^([A-Za-z0-9_.-]+)==([^\s\\;]+)",
        (ROOT / "requirements.txt").read_text("utf-8"),
        re.MULTILINE,
    )

    assert pinned
    for name, version in pinned:
        assert re.search(rf"^{re.escape(name)} {re.escape(version)} ", notice, re.M), (
            f"{name} {version}"
        )
    for component in ("Chromium", "Roboto", "Material Icons", "Liberation", "Noto"):
        assert component in notice, component


def test_the_image_carries_license_and_notice():
    assert "COPY LICENSE NOTICE" in (ROOT / "Dockerfile").read_text("utf-8")


def test_the_readme_names_the_same_license():
    readme = (ROOT / "README.md").read_text("utf-8")
    section = readme.split("## Lizenz", 1)[1]

    assert "Apache License 2.0" in section and "MIT" not in section
