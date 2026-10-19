"""
docker-compose.yml against the chart's runtime rules (audit OPS-15).

- Docker stops a container after 10 s by default; a running /generate takes
  up to 300 s (METADATA_AGENT_GENERATE_TIMEOUT), so compose cut it off.
- The image runs read-only in the cluster; compose ran it writable, so a
  write outside /tmp and the home directory only failed in production.
- In mode auto anyone reaching the port runs extractions on B_API_KEY; the
  port was published on every interface.
"""

import re
from pathlib import Path

import yaml

from src.config import INGRESS_TIMEOUT_SECONDS

ROOT = Path(__file__).resolve().parents[1]


def _service() -> dict:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text("utf-8"))
    return compose["services"]["metadata-agent-api"]


def _seconds(value: str) -> int:
    assert value.endswith("s"), value
    return int(value[:-1])


def test_compose_waits_for_a_running_generation():
    # 320 s since 19.10. (was 120 s), like the chart's grace period.
    assert _seconds(_service()["stop_grace_period"]) >= INGRESS_TIMEOUT_SECONDS


def test_compose_defaults_to_the_generate_timeout_of_the_code():
    environment = _service()["environment"]
    default = "METADATA_AGENT_GENERATE_TIMEOUT=${METADATA_AGENT_GENERATE_TIMEOUT:-300}"

    assert default in environment


def test_documented_examples_wait_for_a_running_generation():
    """`docker run --stop-timeout` and compose snippets follow the grace period."""
    stop_timeouts, grace_periods = [], []
    for name in ("README.md", "DEPLOYMENT.md", "INSTALL.md", "Dockerfile"):
        text = (ROOT / name).read_text("utf-8")
        stop_timeouts += re.findall(r"--stop-timeout (\d+)", text)
        grace_periods += re.findall(r"stop_grace_period: (\d+)s", text)

    assert len(stop_timeouts) >= 6 and len(grace_periods) >= 4
    assert set(stop_timeouts) | set(grace_periods) == {str(INGRESS_TIMEOUT_SECONDS)}


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
