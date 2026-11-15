"""
What the start log warns about, and what it must not print (audit 14.11.).

O-38: three configurations started without a word - provider `openai` without
OPENAI_API_KEY, text extraction and geocoder through the B-API without any
B-API key that could be sent, and the debug endpoints switched on.

The deadline warnings compared with a fixed 320 s; where the grace period is
configured (the Helm chart sets METADATA_AGENT_GRACE_PERIOD_SECONDS from
terminationGracePeriodSeconds), they compare with that.

A URL setting may carry user and password (https://user:secret@host): neither
the start lines nor a warning print them.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from src import main
from src.config import Settings
from conftest import use_settings


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        **{
            "b_api_key": "own-key",
            "wlo_guest_username": "user",
            "wlo_guest_password": "password",
            **overrides,
        },
    )


def _warnings(**overrides) -> list[str]:
    return _settings(**overrides).config_warnings()


def test_the_default_configuration_with_its_keys_warns_about_nothing():
    assert _warnings() == []


# ------------------------------------------------------------ O-38 (a)


def test_provider_openai_without_its_key_is_flagged():
    warnings = _warnings(llm_provider="openai", openai_api_key="")

    assert any("OPENAI_API_KEY" in w and "/ready" in w for w in warnings)


def test_provider_openai_with_its_key_is_not():
    warnings = _warnings(llm_provider="openai", openai_api_key="sk-test")

    assert not any("OPENAI_API_KEY" in w for w in warnings)


def test_a_b_api_provider_needs_no_openai_key():
    assert not any("OPENAI_API_KEY" in w for w in _warnings(openai_api_key=""))


# ------------------------------------------------------------ O-38 (b)


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("static", "scheitern im Modus static"),
        ("auto", "nur bei Aufrufen mit durchgereichtem Schlüssel"),
    ],
)
def test_services_through_the_b_api_without_a_key_to_send_are_flagged(mode, expected):
    warnings = _warnings(
        llm_provider="openai",
        openai_api_key="sk-test",
        b_api_services="proxy",
        b_api_auth_mode=mode,
        b_api_key="",
    )

    flagged = [w for w in warnings if "B_API_KEY" in w]
    assert len(flagged) == 1
    assert "Textextraktion und Geocoder" in flagged[0] and expected in flagged[0]


@pytest.mark.parametrize(
    "overrides",
    [
        {"b_api_services": "proxy", "b_api_key": "own-key"},
        # Every call brings its key.
        {"b_api_services": "proxy", "b_api_auth_mode": "forward", "b_api_key": ""},
        # Nothing goes to the B-API.
        {"b_api_services": "direct", "b_api_key": ""},
        {"b_api_services": "auto", "b_api_key": ""},
    ],
)
def test_no_b_api_key_warning_where_none_is_missing(overrides):
    warnings = _warnings(llm_provider="openai", openai_api_key="sk-test", **overrides)

    assert not any("B_API_KEY" in w for w in warnings)


# ------------------------------------------------------------ O-38 (c)


def test_switched_on_debug_endpoints_are_flagged():
    warnings = _warnings(debug_endpoints=True)

    assert any(
        "METADATA_AGENT_DEBUG_ENDPOINTS" in w and "/debug/" in w for w in warnings
    )


def test_debug_endpoints_are_off_and_silent_by_default():
    assert not any("DEBUG_ENDPOINTS" in w for w in _warnings())


# ------------------------------------------------------------ grace period


def test_without_a_configured_grace_period_the_deadlines_compare_with_320_s():
    warnings = _warnings(generate_timeout=315)

    assert any("GENERATE_TIMEOUT=315" in w and "320 s" in w for w in warnings)


def test_a_raised_grace_period_takes_the_longer_deadline_without_a_warning():
    warnings = _warnings(generate_timeout=500, grace_period_seconds=520)

    assert not any("GENERATE_TIMEOUT" in w for w in warnings)


@pytest.mark.parametrize(
    "name, overrides",
    [
        ("METADATA_AGENT_GENERATE_TIMEOUT=300", {}),
        ("METADATA_AGENT_UPLOAD_TIMEOUT=100", {"generate_timeout": 60}),
    ],
)
def test_a_lowered_grace_period_flags_the_deadline_that_no_longer_fits(name, overrides):
    warnings = _warnings(grace_period_seconds=90, **overrides)

    flagged = [w for w in warnings if w.startswith(name)]
    assert len(flagged) == 1
    assert "90 s" in flagged[0]
    assert "METADATA_AGENT_GRACE_PERIOD_SECONDS" in flagged[0]


def test_an_empty_grace_period_is_not_set(monkeypatch):
    monkeypatch.setenv("METADATA_AGENT_GRACE_PERIOD_SECONDS", "")

    assert Settings(_env_file=None).grace_period_seconds is None


# ------------------------------------------------------------ credentials

SECRET = "s3cr3t-in-the-url"


def _with_credentials() -> Settings:
    return _settings(
        b_api_base_url=f"https://agent:{SECRET}@b-api.example",
        # Not below the B-API address: the warning about it names both.
        b_api_openai_base=f"https://agent:{SECRET}@llm.example/v1",
        repository_url=f"https://agent:{SECRET}@repo.example/edu-sharing/rest",
        text_extraction_api_url=f"http://agent:{SECRET}@text-extraction.svc",
    )


def test_the_start_lines_do_not_print_credentials_of_a_url(monkeypatch, caplog):
    use_settings(monkeypatch, _with_credentials())

    with caplog.at_level(logging.INFO), TestClient(main.app):
        pass

    log = "\n".join(record.getMessage() for record in caplog.records)
    for host in ("b-api.example", "llm.example", "repo.example", "text-extraction.svc"):
        assert host in log, host
    assert SECRET not in log and "agent:" not in log


def test_the_warnings_do_not_print_credentials_of_a_url():
    warnings = " ".join(_with_credentials().config_warnings())

    assert "llm.example" in warnings
    assert SECRET not in warnings


def test_the_calls_keep_the_credentials_of_the_setting():
    """Only the log leaves them out."""
    assert SECRET in _with_credentials().repository_url
