"""
requirements.txt (the pip path in INSTALL.md) installs exactly what uv.lock does.

2.1.0 added prometheus-client to pyproject only; a pip install then failed at
startup with ModuleNotFoundError, because src.main imports src.metrics. Until
3.0.0 the file held lower bounds only, so pip could resolve other versions than
the tested lock (audit DEP-01). It is now `uv export` of the lock, with hashes:
regenerate it with the command in its header whenever uv.lock changes.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = re.compile(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([^\s;\\]+)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _requirement_lines() -> list[str]:
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return [
        line
        for line in lines
        if line and not line[0].isspace() and not line.startswith("#")
    ]


def _pins() -> dict[str, str]:
    pins = {}
    for line in _requirement_lines():
        match = PIN.match(line)
        assert match, f"not an exact pin: {line}"
        pins[_norm(match.group(1))] = match.group(3)
    return pins


def _lock_versions() -> dict[str, str]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {_norm(p["name"]): p["version"] for p in lock["package"] if "version" in p}


def test_every_runtime_dependency_is_in_requirements_txt():
    declared = {
        _norm(re.split(r"[\[<>=!~; ]", dep, maxsplit=1)[0])
        for dep in _pyproject()["project"]["dependencies"]
    }
    assert declared - set(_pins()) == set()


def test_every_pin_is_the_locked_version():
    locked = _lock_versions()
    pins = _pins()

    assert {
        name: version for name, version in pins.items() if locked.get(name) != version
    } == {}


def test_every_pin_carries_a_hash():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=\S)", text)
    unhashed = [
        b.splitlines()[0] for b in blocks if PIN.match(b) and "--hash=sha256:" not in b
    ]

    assert unhashed == []


def test_no_dev_tool_is_installed_in_production():
    def names(deps):
        return {_norm(re.split(r"[\[<>=!~; ]", dep, maxsplit=1)[0]) for dep in deps}

    project = _pyproject()
    # httpx is both: a runtime dependency and listed for the tests.
    dev_only = names(project["dependency-groups"]["dev"]) - names(
        project["project"]["dependencies"]
    )

    assert dev_only & set(_pins()) == set()
