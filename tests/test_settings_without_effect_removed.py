"""
Two settings were read and steered nothing (audit 14.11., O-45).

METADATA_AGENT_SCREENSHOT_METHOD: every endpoint uses the method its request
names. METADATA_AGENT_DEFAULT_CONTEXT: only echoed by GET /info/schemata, while
requests kept defaulting to `default` - setting it made the endpoint report a
default no request had. Both are gone; an environment that still sets them
starts as before, because unknown variables are ignored.
"""

import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src import main
from src.config import Settings
from src.models.schemas import GenerateRequest
from src.services import screenshot_service
from conftest import use_settings

ROOT = Path(__file__).resolve().parents[1]
REMOVED = ("METADATA_AGENT_SCREENSHOT_METHOD", "METADATA_AGENT_DEFAULT_CONTEXT")


def test_the_two_settings_are_gone():
    assert "screenshot_method" not in Settings.model_fields
    assert "default_context" not in Settings.model_fields


def test_an_environment_that_still_sets_them_starts(monkeypatch):
    monkeypatch.setenv("METADATA_AGENT_SCREENSHOT_METHOD", "pageshot")
    monkeypatch.setenv("METADATA_AGENT_DEFAULT_CONTEXT", "mds_oeh")

    Settings(_env_file=None)


def test_an_env_file_that_still_sets_them_starts(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "METADATA_AGENT_SCREENSHOT_METHOD=pageshot\n"
        "METADATA_AGENT_DEFAULT_CONTEXT=mds_oeh\n"
        "METADATA_AGENT_LOG_LEVEL=WARNING\n",
        encoding="utf-8",
    )

    assert Settings(_env_file=env_file).log_level == "WARNING"


@pytest.mark.parametrize(
    "path",
    [".env.template", "docker-compose.yml", "helm/metadata-agent/values.yaml"],
)
def test_the_shipped_configuration_no_longer_offers_them(path):
    text = (ROOT / path).read_text("utf-8")

    for name in REMOVED:
        assert name not in text, name


def test_compose_comments_no_longer_name_the_setting():
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")

    assert not re.search(r"screenshot_method\s*=", compose)
    assert yaml.safe_load(compose)["services"]["metadata-agent-api"]


def test_info_schemata_reports_the_context_requests_default_to(monkeypatch):
    # As an old deployment set it: without effect on the answer.
    old = Settings(_env_file=None, default_context="mds_oeh")
    use_settings(monkeypatch, old)

    with TestClient(main.app) as client:
        info = client.get("/info/schemata").json()

    assert info["default_context"] == GenerateRequest(text="x").context == "default"
    assert info["default_context"] in {c["name"] for c in info["contexts"]}


def test_the_screenshot_service_falls_back_to_the_local_method():
    old = Settings(_env_file=None, screenshot_method="pagshot")
    service = screenshot_service.ScreenshotService(old)

    assert service.default_method == screenshot_service.LOCAL_METHODS[0] == "playwright"
