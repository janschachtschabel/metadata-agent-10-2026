"""
Settings that cannot work are refused at start, not on every call.

METADATA_AGENT_LLM_MAX_RETRIES=0 left the transport zero attempts: every LLM
call failed as "HTTP None" (audit 18.10. final). The value counts the attempts
of one call (1 = no repetition) and the extra calls for an invalid value.

Audit 14.11., O-39: the same for choices and numbers that only failed on the
request or meant something else - a misspelt reasoning effort was sent on to
the model, a rate of -1 switched the rate cap off, a timeout of 0 failed every
call, an allow-list of " , " allowed every model.
"""

import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.config import EMPTY_IS_A_VALUE, Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("value", [0, -1])
def test_an_llm_call_needs_at_least_one_attempt(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_max_retries=value)


def test_one_attempt_is_allowed():
    assert Settings(_env_file=None, llm_max_retries=1).llm_max_retries == 1


# ------------------------------------------------------------ audit 14.11., O-39


@pytest.mark.parametrize(
    "name, value",
    [
        ("llm_verbosity", "hihg"),
        ("llm_verbosity", "none"),  # an effort, no verbosity
        ("llm_reasoning_effort", "hihg"),
        ("llm_reasoning_effort", "minimal"),  # the API rejects it
    ],
)
def test_an_unknown_verbosity_or_effort_is_refused(name, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


@pytest.mark.parametrize(
    "name, written, read",
    [
        ("llm_verbosity", " High ", "high"),
        ("llm_reasoning_effort", "NONE", "none"),
        # Empty is a value here: the parameter is not sent.
        ("llm_verbosity", "", ""),
        ("llm_reasoning_effort", "  ", ""),
    ],
)
def test_verbosity_and_effort_are_read_without_blanks_or_case(name, written, read):
    assert getattr(Settings(_env_file=None, **{name: written}), name) == read


@pytest.mark.parametrize(
    "name, value",
    [
        ("llm_max_requests_per_second", "-1"),
        ("llm_max_concurrent_requests", "0"),
        ("llm_max_concurrent_requests", "-2"),
        ("request_timeout", "0"),
        ("request_timeout", "-5"),
        ("llm_max_tokens", "0"),
        ("llm_temperature", "9"),
        ("llm_temperature", "-0.1"),
        ("openai_temperature", "9"),
        ("llm_retry_delay", "-1"),
        ("screenshot_width", "0"),
        ("screenshot_height", "100000"),
        ("screenshot_delay", "-1"),
    ],
)
def test_a_number_that_cannot_work_is_refused(name, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


def test_a_rate_of_zero_still_means_no_cap():
    settings = Settings(_env_file=None, llm_max_requests_per_second="0")

    limits = settings.get_throughput_limits("b-api-academiccloud")

    assert limits["requests_per_second"] == 0.0


@pytest.mark.parametrize("value", [" , ", ",", " ,, "])
def test_an_allow_list_that_names_no_model_is_refused(value):
    with pytest.raises(ValidationError, match="ALLOWED_LLM_MODELS"):
        Settings(_env_file=None, allowed_llm_models=value)


@pytest.mark.parametrize("value", ["", "  "])
def test_a_blank_allow_list_still_allows_every_model(value):
    settings = Settings(_env_file=None, allowed_llm_models=value)

    assert settings.allowed_llm_model_names() == set()


def test_an_allow_list_with_a_stray_comma_keeps_its_names():
    settings = Settings(_env_file=None, allowed_llm_models="gpt-6-luna, ,qwen")

    assert settings.allowed_llm_model_names() == {"gpt-6-luna", "qwen"}


# The files a deployment starts from must still start: their values pass the
# bounds, and where they name a default it is the code's.

_SECRETS = {"B_API_KEY", "OPENAI_API_KEY", "WLO_GUEST_USERNAME", "WLO_GUEST_PASSWORD"}


def _env_template() -> dict[str, str]:
    text = (ROOT / ".env.template").read_text("utf-8")
    return dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, re.MULTILINE))


def _compose_defaults() -> dict[str, str]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text("utf-8"))
    environment = compose["services"]["metadata-agent-api"]["environment"]
    defaults = {}
    for entry in environment:
        name, _, value = entry.partition("=")
        match = re.fullmatch(r"\$\{" + name + r":?-(.*)\}", value)
        assert match, entry
        defaults[name] = match.group(1)
    return defaults


def _chart_env() -> dict[str, str]:
    values = yaml.safe_load(
        (ROOT / "helm" / "metadata-agent" / "values.yaml").read_text("utf-8")
    )
    return {name: str(value) for name, value in values["env"].items()}


def _field_of(variable: str) -> str:
    for name, field in Settings.model_fields.items():
        if variable in (field.alias, f"METADATA_AGENT_{name.upper()}"):
            return name
    raise AssertionError(f"{variable} is no setting")


@pytest.mark.parametrize(
    "source", [_env_template, _compose_defaults, _chart_env], ids=lambda f: f.__name__
)
def test_the_shipped_configuration_files_pass_the_bounds(monkeypatch, source):
    for variable, value in source().items():
        monkeypatch.setenv(variable, value)

    Settings(_env_file=None)


@pytest.mark.parametrize(
    "source", [_env_template, _compose_defaults], ids=lambda f: f.__name__
)
def test_env_template_and_compose_start_with_the_defaults_of_the_code(source):
    """The chart is left out: it configures production on purpose."""
    for variable, value in source().items():
        if variable in _SECRETS:
            continue
        name = _field_of(variable)
        default = Settings.model_fields[name].default
        if value == "" and name not in EMPTY_IS_A_VALUE:
            continue  # empty: the default applies
        written = Settings(_env_file=None, **{name: value})
        assert getattr(written, name) == getattr(Settings(_env_file=None), name), (
            variable,
            value,
            default,
        )
