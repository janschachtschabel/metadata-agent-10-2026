"""
NOTICE names what the image really ships (audit 14.11., O-44).

It listed `colorama`, which only Windows installs and the image does not, and
left out two things that come with playwright: the FFmpeg build next to the
browser and the Node.js binary of its driver. tests/test_license.py checks that
every package of requirements.txt is named; this checks the other direction and
the browser components `playwright install` reports.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICE = (ROOT / "NOTICE").read_text("utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text("utf-8")


def _section(title: str) -> str:
    """The text below an underlined heading, up to the next one."""
    start = NOTICE.index(f"\n{title}")
    body = NOTICE[start:].split("\n", 3)[3]
    following = re.search(r"\n[^\n]+\n-{3,}\n", body)
    return body[: following.start()] if following else body


def test_no_python_package_is_named_that_the_image_does_not_install():
    installed = set(
        re.findall(
            r"^([A-Za-z0-9_.-]+)==",
            (ROOT / "requirements.txt").read_text("utf-8"),
            re.MULTILINE,
        )
    )
    named = set(
        re.findall(r"^([A-Za-z0-9_.-]+)  +\S", _section("Python packages"), re.M)
    )

    assert named and named <= installed, sorted(named - installed)


def test_everything_the_browser_installation_downloads_is_named():
    """As the Dockerfile installs it; `--dry-run` only prints what would be fetched."""
    arguments = re.search(r"playwright install (--\S+ chromium)", DOCKERFILE).group(1)
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "playwright",
            "install",
            "--dry-run",
            *arguments.split(),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    components = re.findall(r"^(\S[^\n]*?) [\d.]*\s*\(playwright ", out, re.MULTILINE)

    assert "FFmpeg" in components and len(components) >= 2, out
    for component in components:
        if component == "Winldd":  # a helper of the Windows build only
            continue
        assert component in _section("Browser"), component
    assert arguments in _section("Browser")


def test_the_node_runtime_of_the_playwright_driver_is_named():
    assert re.search(
        r"^Node\.js \(.*playwright", _section("Runtime and system packages"), re.M
    )


def test_every_debian_package_of_the_dockerfile_is_named():
    install = DOCKERFILE[DOCKERFILE.index("apt-get install") :].split("&&", 1)[0]
    packages = [
        word for word in install.split()[2:] if re.fullmatch(r"[a-z][a-z0-9.+-]+", word)
    ]

    assert "tini" in packages and len(packages) > 15
    for package in packages:
        assert re.search(rf"\b{re.escape(package)}\b", NOTICE), package
