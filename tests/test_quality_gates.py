"""
Coverage and complexity have a limit, in both CI systems (audits T-01, M-01).

Without a number nobody sees a module drop out of the tests - the 3.0.0 split
created a dozen modules that were tested only through others. The floor sits
just under the measured value, so it stops a regression without blocking work.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _coverage_config() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "coverage"
    ]


def test_coverage_has_a_floor_and_counts_branches():
    config = _coverage_config()

    assert config["run"]["branch"] is True
    assert config["run"]["source"] == ["src"]
    assert config["report"]["fail_under"] >= 70


def test_pytest_cov_is_a_dev_dependency():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert any(
        dep.startswith("pytest-cov") for dep in pyproject["dependency-groups"]["dev"]
    )


def test_both_ci_systems_run_the_suite_with_coverage():
    for ci in (".github/workflows/docker-publish.yml", ".gitlab-ci.yml"):
        text = (ROOT / ci).read_text(encoding="utf-8")
        assert "uv run pytest -q -rs --cov" in text, ci


def test_ruff_fails_functions_past_the_complexity_ceiling():
    """Audit M-01: no new god functions; the ceiling only ever goes down."""
    lint = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "ruff"
    ]["lint"]

    assert "C901" in lint["extend-select"]
    assert lint["mccabe"]["max-complexity"] <= 15


def test_mypy_checks_all_of_src_in_both_ci_systems():
    """Audits M-04 and T-07: every module is type-checked, not only the mixins."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mypy = pyproject["tool"]["mypy"]

    assert any(dep.startswith("mypy") for dep in pyproject["dependency-groups"]["dev"])
    assert mypy["files"] == ["src"]
    for ci in (".github/workflows/docker-publish.yml", ".gitlab-ci.yml"):
        assert "uv run mypy" in (ROOT / ci).read_text(encoding="utf-8"), ci


def test_both_ci_systems_run_ruff():
    """The complexity ceiling lives in pyproject; it only bites where ruff runs."""
    github = (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    gitlab = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")

    assert "ruff check" in github and "ruff format --check" in github
    assert "ruff check" in gitlab and "ruff format --diff" in gitlab
