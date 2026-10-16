"""
requirements.txt (the pip path in INSTALL.md) lists what pyproject.toml lists.

2.1.0 added prometheus-client to pyproject only; a pip install then failed at
startup with ModuleNotFoundError, because src.main imports src.metrics.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _name(requirement: str) -> str:
    return re.split(r"[\[<>=!~; ]", requirement.strip(), maxsplit=1)[0].lower()


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _requirements() -> set[str]:
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return {_name(line) for line in lines if line.strip() and not line.startswith("#")}


def test_every_runtime_dependency_is_in_requirements_txt():
    declared = {_name(dep) for dep in _pyproject()["project"]["dependencies"]}
    assert declared - _requirements() == set()


def test_requirements_txt_holds_nothing_pyproject_does_not_know():
    project = _pyproject()
    known = {_name(dep) for dep in project["project"]["dependencies"]}
    known |= {_name(dep) for dep in project["tool"]["uv"]["constraint-dependencies"]}
    assert _requirements() - known == set()
