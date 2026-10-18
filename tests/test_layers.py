"""
Import direction between the packages of src/ (audit T-05).

The API layer (src/api: routers, dependencies) sits on top. Services, utils
and models never import it: a service that reaches into a router or into
deps drags FastAPI request handling into code the CLI scripts and tests use
on their own, and opens import cycles. Read from the source (AST), so a
lazy import inside a function counts as well.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
LOWER_LAYERS = ("services", "utils", "models")


def _module_name(path: Path) -> str:
    parts = path.relative_to(SRC.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(path: Path) -> set[str]:
    """Absolute names of every module the file imports, relative ones resolved."""
    package = _module_name(path)
    if path.name != "__init__.py":
        package = package.rpartition(".")[0]
    names = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def _files(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


def test_the_resolver_sees_relative_imports():
    deps = SRC / "api" / "deps.py"

    assert any(name.startswith("src.services") for name in _imports(deps))


@pytest.mark.parametrize("layer", LOWER_LAYERS)
def test_lower_layers_never_import_the_api(layer):
    offenders = {
        str(path.relative_to(SRC)): sorted(
            name
            for name in _imports(path)
            if name == "src.api" or name.startswith("src.api.")
        )
        for path in _files(layer)
    }

    assert _files(layer), layer
    assert not {file: names for file, names in offenders.items() if names}


@pytest.mark.parametrize("layer", ("models", "utils"))
def test_models_and_utils_never_import_the_services(layer):
    """Audit M-05: the request models imported repository_curation."""
    offenders = {
        str(path.relative_to(SRC)): sorted(
            name
            for name in _imports(path)
            if name == "src.services" or name.startswith("src.services.")
        )
        for path in _files(layer)
    }

    assert not {file: names for file, names in offenders.items() if names}


ROOT_MODULES = ("config.py", "metrics.py", "infra.py")


@pytest.mark.parametrize("module", ROOT_MODULES)
def test_root_modules_never_import_the_api_or_the_services(module):
    """
    Audit 18.10. final: config, metrics and infra sit below everything - the
    services read the settings, the internal port serves /metrics.
    """
    names = _imports(SRC / module)

    assert not sorted(
        name
        for name in names
        if name.split(".")[:2] in (["src", "api"], ["src", "services"])
    )


def test_nothing_imports_the_application_module():
    """src.main builds the app at import time; importing it is a side effect."""
    offenders = sorted(
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path.name != "main.py"
        and any(
            name == "src.main" or name.startswith("src.main.")
            for name in _imports(path)
        )
    )

    assert not offenders
