"""
docker-compose.yml against the chart's runtime rules (audit OPS-15).

- Docker stops a container after 10 s by default; a running /generate takes
  up to 110 s (METADATA_AGENT_GENERATE_TIMEOUT), so compose cut it off.
- The image runs read-only in the cluster; compose ran it writable, so a
  write outside /tmp and the home directory only failed in production.
- In mode auto anyone reaching the port runs extractions on B_API_KEY; the
  port was published on every interface.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _service() -> dict:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text("utf-8"))
    return compose["services"]["metadata-agent-api"]


def _seconds(value: str) -> int:
    assert value.endswith("s"), value
    return int(value[:-1])


def test_compose_waits_for_a_running_generation():
    assert _seconds(_service()["stop_grace_period"]) >= 120


def test_compose_runs_read_only_like_the_chart():
    service = _service()

    assert service["read_only"] is True
    assert set(service["tmpfs"]) == {"/tmp", "/home/appuser"}


def test_compose_publishes_the_port_on_localhost_only():
    assert _service()["ports"] == ["127.0.0.1:8000:8000"]


# Review 18.10.: compose passed only its explicit list, so settings offered in
# .env.template (B_API_IGNORE_CACHE, SCREENSHOT_*, B_API_ROUTER_BASE, ...) and
# the new METADATA_AGENT_UPLOAD_TIMEOUT never reached the container.


def test_compose_passes_the_whole_env_file_when_there_is_one():
    assert _service()["env_file"] == [{"path": ".env", "required": False}]


def test_compose_names_the_upload_timeout():
    environment = _service()["environment"]

    assert any(e.startswith("METADATA_AGENT_UPLOAD_TIMEOUT=") for e in environment)
