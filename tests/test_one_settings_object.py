"""
Routers and services read one settings object, and a test changes it in one
place (audit 14.11.2026, Q-09).

There were three ways to the settings - `deps.settings` (a module attribute of
its own), `get_settings()` in the services, and `settings` as an argument -
and the tests overrode each differently: 18 places replaced `deps.settings`,
42 patched the `get_settings` of one module. Such a test ran the routers on
one configuration and the services on another, which no deployment can do.

Now `deps.settings` is `get_settings()` (looked up at every use), and
`use_settings` in tests/conftest.py puts another object in force for all of
them. The guard below refuses the two older ways in any test module.
"""

import ast
import importlib
from pathlib import Path

import pytest
from conftest import use_settings

import src.config
from src.api import deps
from src.config import Settings, get_settings
from src.services.llm_service import LLMService, reset_provider_gates

TESTS = Path(__file__).parent
SRC = TESTS.parent / "src"

# Test modules that may keep one of the older ways, with the reason. A test
# that needs two different settings objects on purpose would belong here.
EXEMPT: dict[str, str] = {}


# ------------------------------------------------------------------ the seam


def test_deps_settings_is_the_settings_object_of_the_process():
    assert deps.settings is get_settings()
    assert "settings" not in vars(deps)


def test_other_names_of_deps_are_still_missing_loudly():
    with pytest.raises(AttributeError, match="gibt_es_nicht"):
        deps.gibt_es_nicht


def test_every_module_reads_the_settings_through_the_same_function():
    readers = []
    for path in sorted(SRC.rglob("*.py")):
        if "get_settings" not in path.read_text(encoding="utf-8"):
            continue
        name = ".".join(path.relative_to(SRC.parent).with_suffix("").parts)
        if name == "src.main":
            continue
        module = importlib.import_module(name)
        if hasattr(module, "get_settings"):
            readers.append(name)
            assert module.get_settings is src.config.get_settings, name

    assert len(readers) >= 9, readers


def test_a_swap_reaches_routers_and_services_alike(monkeypatch):
    before = get_settings()
    custom = Settings(_env_file=None, allowed_llm_models="nur-dieses", b_api_key="k")

    with monkeypatch.context() as patch:
        assert use_settings(patch, custom) is custom

        assert deps.settings is custom
        assert get_settings() is custom
        # A router-side check and a service built now read the same object.
        with pytest.raises(Exception) as refused:
            deps.check_model_override(None, "ein-anderes")
        assert getattr(refused.value, "status_code", None) == 422
        reset_provider_gates()
        assert LLMService().settings is custom

    assert deps.settings is before
    assert get_settings() is before
    reset_provider_gates()


def test_without_an_object_the_environment_is_read_again(monkeypatch):
    before = get_settings()

    with monkeypatch.context() as patch:
        patch.setenv("METADATA_AGENT_SERVICE_NAME", "aus-der-umgebung")
        use_settings(patch)

        fresh = get_settings()
        assert fresh is not before
        assert fresh.service_name == "aus-der-umgebung"
        assert deps.settings is fresh
        assert get_settings() is fresh

    assert get_settings() is before


def test_a_single_value_goes_onto_the_object_in_force(monkeypatch):
    monkeypatch.setattr(deps.settings, "upload_timeout", 7.5)

    assert get_settings().upload_timeout == 7.5


# ------------------------------------------------------------------ the guard


def _older_ways(source: str) -> list[str]:
    """Lines of `source` that replace deps.settings or patch a module's get_settings."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and (
                    target.attr == "get_settings"
                    or (
                        target.attr == "settings"
                        and ast.unparse(target.value).endswith("deps")
                    )
                ):
                    found.append(f"{node.lineno}: {ast.unparse(target)} = ...")
        if not isinstance(node, ast.Call):
            continue
        constants = [
            arg.value
            for arg in node.args[:2]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        ]
        called = ast.unparse(node.func)
        patches = called.endswith(("setattr", "patch", "patch.object"))
        if not patches:
            continue
        for value in constants:
            # setattr(module, "get_settings", ...), patch("pkg.module.get_settings")
            if value == "get_settings" or value.endswith(".get_settings"):
                found.append(f"{node.lineno}: {called}(... {value!r} ...)")
            # setattr("src.api.deps.settings", ...)
            elif value.endswith("deps.settings"):
                found.append(f"{node.lineno}: {called}(... {value!r} ...)")
            # setattr(deps, "settings", ...)
            elif (
                value == "settings"
                and node.args
                and not isinstance(node.args[0], ast.Constant)
                and ast.unparse(node.args[0]).endswith("deps")
            ):
                found.append(f"{node.lineno}: {called}(deps, 'settings', ...)")
    return found


@pytest.mark.parametrize(
    "line",
    [
        'monkeypatch.setattr(deps, "settings", cfg)',
        'monkeypatch.setattr(generate_module.deps, "settings", cfg)',
        'monkeypatch.setattr("src.api.deps.settings", cfg)',
        "deps.settings = cfg",
        'setattr(deps, "settings", cfg)',
        'monkeypatch.setattr(llm_module, "get_settings", lambda: cfg)',
        'monkeypatch.setattr("src.services.llm_service.get_settings", lambda: cfg)',
        'mock.patch("src.config.get_settings", return_value=cfg)',
        'mock.patch.object(geo_module, "get_settings", lambda: cfg)',
        "llm_module.get_settings = lambda: cfg",
    ],
)
def test_the_guard_finds_each_of_the_older_ways(line):
    assert len(_older_ways(f"def test_x(monkeypatch):\n    {line}\n")) == 1


@pytest.mark.parametrize(
    "line",
    [
        "use_settings(monkeypatch, cfg)",
        'monkeypatch.setattr(deps.settings, "upload_timeout", 5)',
        'monkeypatch.setattr(deps, "get_metadata_service", lambda **kw: service)',
        'monkeypatch.setattr(service, "settings", cfg)',
        "service.settings = cfg",
        "create_internal_app(get_settings)",
    ],
)
def test_the_guard_leaves_the_supported_ways_alone(line):
    assert _older_ways(f"def test_x(monkeypatch):\n    {line}\n") == []


def test_no_test_module_overrides_the_settings_past_the_seam():
    modules = sorted(TESTS.glob("*.py"))
    offenders = {
        path.name: found
        for path in modules
        if path.name not in EXEMPT
        and (found := _older_ways(path.read_text(encoding="utf-8")))
    }

    assert len(modules) > 200
    assert offenders == {}, (
        f"use_settings(monkeypatch, ...) from tests/conftest.py instead: {offenders}"
    )


def test_every_exemption_names_a_module_that_needs_it():
    for name, reason in EXEMPT.items():
        assert reason.strip(), name
        assert _older_ways((TESTS / name).read_text(encoding="utf-8")), name
