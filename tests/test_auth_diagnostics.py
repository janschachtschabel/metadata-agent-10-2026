"""
Diagnosing the auth chain without ever exposing a key.

/debug/auth answers whether a forwarded key arrives - masked, and only when
switched on (METADATA_AGENT_DEBUG_ENDPOINTS). /health names the auth mode.
At startup the agent warns about settings that cannot work together.
"""

from fastapi.testclient import TestClient

from src import main
from src.config import Settings

KEY = "Bearer 0123456789abcdef0123456789abcdef"


def _client(monkeypatch, **overrides):
    monkeypatch.setattr(
        main, "settings", Settings(_env_file=None, b_api_key="own-key", **overrides)
    )
    return TestClient(main.app)


def test_debug_auth_does_not_exist_unless_switched_on(monkeypatch):
    with _client(monkeypatch) as client:
        assert (
            client.get("/debug/auth", headers={"Authorization": KEY}).status_code == 404
        )


def test_debug_auth_shows_presence_and_length_never_the_key(monkeypatch):
    with _client(monkeypatch, debug_endpoints=True) as client:
        response = client.get("/debug/auth", headers={"Authorization": KEY})

    data = response.json()
    assert response.status_code == 200
    assert data["Authorization"] == {"present": True, "scheme": "Bearer", "length": 32}
    assert data["X-API-KEY"] == {"present": False}
    assert data["mode"] == "auto"
    assert data["llm_calls_use"] == "forwarded"
    assert "0123456789abcdef" not in response.text
    assert "own-key" not in response.text


def test_debug_auth_tells_when_the_own_key_would_be_used(monkeypatch):
    with _client(monkeypatch, debug_endpoints=True) as client:
        data = client.get("/debug/auth").json()

    assert data["llm_calls_use"] == "static"
    assert data["b_api_key_configured"] is True


def test_health_names_provider_and_auth_mode_but_no_secret(monkeypatch):
    with _client(monkeypatch, b_api_auth_mode="forward") as client:
        response = client.get("/health")

    assert response.json()["b_api_auth_mode"] == "forward"
    assert "llm_provider" in response.json()
    assert "own-key" not in response.text


# ------------------------------------------------------------ startup warnings


def _warnings(**overrides):
    return Settings(_env_file=None, **overrides).config_warnings()


def test_static_mode_without_key_is_flagged():
    assert any(
        "B_API_KEY" in w for w in _warnings(b_api_auth_mode="static", b_api_key="")
    )


def test_a_key_that_forward_mode_never_uses_is_flagged():
    assert any(
        "forward" in w for w in _warnings(b_api_auth_mode="forward", b_api_key="k")
    )


def test_mixed_staging_and_production_addresses_are_flagged():
    warnings = _warnings(
        b_api_key="k",
        b_api_base_url="https://b-api.prod.openeduhub.net",
        repository_url="https://repository.staging.openeduhub.net/edu-sharing/rest",
    )

    assert any("Staging" in w and "Produktion" in w for w in warnings)


def test_a_consistent_configuration_starts_quietly():
    assert _warnings(b_api_key="k") == []
