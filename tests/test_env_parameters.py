"""
ENV-PARAMETER.md is the operator-facing contract of this service - in English
(the default) and in German (ENV-PARAMETER.de.md); both are checked.

A setting that exists in code but not in the document is invisible to whoever
deploys it — and one that is documented but no longer read is worse, because
setting it looks like it works. Neither is caught by anything else, so it is
checked here against the model itself rather than against a hand-kept list.
"""

import re
from pathlib import Path

import pytest

from src.config import Settings
from conftest import use_settings

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [ROOT / "ENV-PARAMETER.md", ROOT / "ENV-PARAMETER.de.md"]
in_both_languages = pytest.mark.parametrize(
    "document", DOCUMENTS, ids=[d.name for d in DOCUMENTS]
)

# Secrets keep their bare name; everything else takes the prefix.
PREFIX = "METADATA_AGENT_"


def _configured_variables() -> set[str]:
    names = set()
    for name, field in Settings.model_fields.items():
        names.add(field.alias or f"{PREFIX}{name.upper()}")
    return names


def _documented_variables(document: Path) -> set[str]:
    text = document.read_text(encoding="utf-8")
    return set(
        re.findall(
            r"\b(?:METADATA_AGENT_[A-Z0-9_]+|B_API_KEY|OPENAI_API_KEY|WLO_GUEST_[A-Z]+)\b",
            text,
        )
    )


@in_both_languages
def test_every_setting_is_documented(document):
    missing = sorted(_configured_variables() - _documented_variables(document))

    assert not missing, f"missing in {document.name}: {missing}"


@in_both_languages
def test_no_documented_variable_has_gone_away(document):
    """A documented setting the code stopped reading is a silent no-op."""
    stale = sorted(_documented_variables(document) - _configured_variables())

    assert not stale, f"in {document.name}, but no longer in Settings: {stale}"


def test_the_env_template_only_names_variables_that_exist():
    template = (ROOT / ".env.template").read_text(encoding="utf-8")
    named = set(
        re.findall(
            r"^#?\s*(METADATA_AGENT_[A-Z0-9_]+|B_API_KEY|OPENAI_API_KEY|WLO_GUEST_[A-Z]+)=",
            template,
            re.MULTILINE,
        )
    )

    stale = sorted(named - _configured_variables())

    assert not stale, f"in .env.template, aber nicht in Settings: {stale}"


def _template_values() -> dict[str, str]:
    template = (ROOT / ".env.template").read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", template, re.MULTILINE))


def test_the_env_template_names_every_deadline():
    """Review 18.10. #19: METADATA_AGENT_UPLOAD_TIMEOUT was missing."""
    values = _template_values()

    for name in ("REQUEST_TIMEOUT", "GENERATE_TIMEOUT", "UPLOAD_TIMEOUT"):
        assert f"{PREFIX}{name}" in values, name


def test_the_env_template_offers_the_default_generate_timeout():
    # 300 s since 19.10. (was 110 s); a copied template must not undo it.
    assert _template_values()[f"{PREFIX}GENERATE_TIMEOUT"] == "300"


def test_the_env_template_leaves_the_keys_empty():
    """A placeholder key passes the readiness check and fails on the first call."""
    values = _template_values()

    assert values["OPENAI_API_KEY"] == ""
    assert values["B_API_KEY"] == ""


# ------------------------------------------------------- unset and empty values


def test_without_any_variable_every_setting_has_its_default(monkeypatch):
    for name in _configured_variables():
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.b_api_auth_mode == "auto"
    assert settings.llm_provider == "b-api-openai"


# Empty is what these are set to on purpose: "do not send the parameter" and
# "no origin may call" (audit 13.11., L-14).
EMPTY_IS_A_VALUE = {"llm_verbosity", "llm_reasoning_effort", "cors_origins"}


@pytest.mark.parametrize("name", sorted(EMPTY_IS_A_VALUE))
def test_where_empty_is_a_value_it_stays_empty(monkeypatch, name):
    monkeypatch.setenv(f"{PREFIX}{name.upper()}", "")

    assert getattr(Settings(_env_file=None), name) == ""


def test_an_empty_verbosity_and_effort_are_not_sent(monkeypatch):
    """ENV-PARAMETER.md: 'Leer = nicht senden' - through the settings, as deployed."""
    from src.services import llm_service as llm_module

    monkeypatch.setenv(f"{PREFIX}LLM_VERBOSITY", "")
    monkeypatch.setenv(f"{PREFIX}LLM_REASONING_EFFORT", "")
    use_settings(monkeypatch, Settings(_env_file=None))

    service = llm_module.LLMService()

    assert (service.verbosity, service.reasoning_effort) == ("", "")


@pytest.mark.parametrize("name", sorted(set(Settings.model_fields) - EMPTY_IS_A_VALUE))
def test_an_empty_variable_means_the_default(monkeypatch, name):
    """`VAR=` in a .env file or `VAR: ""` in the chart values is 'not set'."""
    variable = Settings.model_fields[name].alias or f"{PREFIX}{name.upper()}"
    monkeypatch.delenv(variable, raising=False)
    unset = getattr(Settings(_env_file=None), name)

    monkeypatch.setenv(variable, "")

    assert getattr(Settings(_env_file=None), name) == unset
