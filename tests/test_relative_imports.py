"""
Every relative import in src/ resolves - module and imported names - also the
ones inside functions.

A function-local import runs only when its code path does; moving a module to
another package (src/main.py -> src/api/routers/ in 3.0.0) silently breaks
`from .services...` there, and so does a misspelt name. Neither ruff nor a test
that skips the path notices. Resolving them statically catches it for every
module at once.
"""

import ast
import importlib
import importlib.util
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _package_of(path: Path) -> str:
    return ".".join(path.relative_to(SRC.parent).with_suffix("").parts[:-1])


def _unresolvable(source: str, package: str) -> list[str]:
    """`from .x import y` statements in `source` whose module or name does not exist."""
    problems = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.ImportFrom) and node.level):
            continue
        relative = "." * node.level + (node.module or "")
        try:
            target = importlib.util.resolve_name(relative, package)
            module = importlib.import_module(target)
        except (ImportError, ValueError):
            problems.append(f"{node.lineno}: {relative}")
            continue
        for alias in node.names:
            if hasattr(module, alias.name):
                continue
            # `from . import deps` names a submodule; only packages have those.
            is_package = hasattr(module, "__path__")
            if not (is_package and importlib.util.find_spec(f"{target}.{alias.name}")):
                problems.append(f"{node.lineno}: {relative} import {alias.name}")
    return problems


def test_every_relative_import_resolves():
    broken = [
        f"{path.relative_to(SRC.parent)}:{problem}"
        for path in sorted(SRC.rglob("*.py"))
        for problem in _unresolvable(
            path.read_text(encoding="utf-8"), _package_of(path)
        )
    ]

    assert not broken, "nicht auflösbar: " + ", ".join(broken)


def test_a_misspelt_name_inside_a_function_is_found():
    source = (
        "def rarely_called():\n"
        "    from ...services.repository_service import get_repository_confg\n"
    )

    assert _unresolvable(source, "src.api.routers") == [
        "2: ...services.repository_service import get_repository_confg"
    ]


def test_a_wrong_package_depth_is_found():
    source = "def rarely_called():\n    from .services.repository_service import RepositoryService\n"

    assert _unresolvable(source, "src.api.routers") == [
        "2: .services.repository_service"
    ]
