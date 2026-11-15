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

import pytest
import yaml

from src.config import EMPTY_IS_A_VALUE, INGRESS_TIMEOUT_SECONDS, Settings

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


# The documents a reader copies commands from, per language; the Dockerfile's
# header carries a `docker run` too.
DOCUMENTS = ("README", "DEPLOYMENT", "INSTALL")
STOP_TIMEOUT = f"--stop-timeout {INGRESS_TIMEOUT_SECONDS}"
GRACE_PERIOD = f"stop_grace_period: {INGRESS_TIMEOUT_SECONDS}s"


def _documents(suffix: str) -> dict[str, str]:
    names = [f"{d}{suffix}" for d in DOCUMENTS] + ["Dockerfile"]
    return {name: (ROOT / name).read_text("utf-8") for name in names}


def _service_starts(text: str) -> list[str]:
    """Each `docker run -d ...` command, whole: up to the end of its block."""
    commands = []
    for match in re.finditer(r"docker run -d\b", text):
        rest = text[match.start() :]
        # A command ends at the code fence, at an empty line, or (Dockerfile
        # header) at an empty comment line.
        end = re.search(r"\n```|\n\s*\n|\n#\s*\n", rest)
        commands.append(rest[: end.start()] if end else rest)
    return commands


def _compose_services(text: str) -> list[str]:
    """Each fenced YAML block that defines a compose service."""
    blocks = re.findall(r"```ya?ml\n(.*?)```", text, re.S)
    return [b for b in blocks if re.search(r"^services:|^ +container_name:", b, re.M)]


def _examples_that_cut_a_generation_off(texts: dict[str, str]) -> list[str]:
    """
    Every example on its own (audit 14.11., Q-05): one `--stop-timeout`
    anywhere in a document used to cover a `docker run` beside it without.
    """
    problems = []
    starts = snippets = 0
    for name, text in texts.items():
        for command in _service_starts(text):
            starts += 1
            if STOP_TIMEOUT not in command:
                problems.append(f"{name}: {command[:60]!r} without {STOP_TIMEOUT}")
        for block in _compose_services(text):
            snippets += 1
            if GRACE_PERIOD not in block:
                problems.append(f"{name}: {block[:60]!r} without {GRACE_PERIOD}")
        for value in re.findall(r"--stop-timeout (\d+)", text) + re.findall(
            r"stop_grace_period: (\d+)s", text
        ):
            if int(value) != INGRESS_TIMEOUT_SECONDS:
                problems.append(f"{name}: timeout {value}")
    # Five commands in the documents and the Dockerfile's, three snippets: a
    # check that finds nothing would pass.
    if starts < 6 or snippets < 3:
        problems.append(f"found only {starts} docker run and {snippets} snippets")
    return problems


@pytest.mark.parametrize("suffix", [".md", ".de.md"])
def test_documented_examples_wait_for_a_running_generation(suffix):
    """`docker run --stop-timeout` and compose snippets follow the grace period."""
    assert _examples_that_cut_a_generation_off(_documents(suffix)) == []


@pytest.mark.parametrize("suffix", [".md", ".de.md"])
@pytest.mark.parametrize(
    "option", ["  --stop-timeout 320 \\\n", "    stop_grace_period: 320s\n"]
)
def test_one_example_without_the_stop_timeout_is_noticed(suffix, option):
    texts = _documents(suffix)
    install = f"INSTALL{suffix}"
    assert option in texts[install]
    # The first of two examples in the document loses the option.
    texts[install] = texts[install].replace(option, "", 1)

    problems = _examples_that_cut_a_generation_off(texts)

    assert len(problems) == 1 and problems[0].startswith(install), problems


def test_compose_keeps_an_emptied_setting_empty():
    """
    `${VAR:-default}` also applies when VAR is empty: the three settings an
    operator empties on purpose came back as their defaults (audit 14.11., O-31).
    """
    environment = dict(e.split("=", 1) for e in _service()["environment"])

    for field in sorted(EMPTY_IS_A_VALUE):
        name = f"METADATA_AGENT_{field.upper()}"
        default = Settings.model_fields[field].default
        assert environment[name] == f"${{{name}-{default}}}", name


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
