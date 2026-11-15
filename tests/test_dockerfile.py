"""
The image is reproducible and the application code is not writable (audit OPS-02).

`chown -R appuser /app` copied the whole virtualenv into another layer and
made the application code writable for the process serving requests; base
images by tag could change under an unchanged Dockerfile.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
INSTALL_BROWSER = "playwright install --only-shell chromium"
SMOKE = "scripts/smoke-image.sh"


def test_base_images_are_pinned_by_digest():
    images = re.findall(r"^(?:FROM|COPY --from=)\s*(\S+)", DOCKERFILE, re.MULTILINE)
    external = [i for i in images if ":" in i or "/" in i]

    assert external, "no base images found"
    for image in external:
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), image


def test_the_application_code_stays_owned_by_root():
    assert "chown -R" not in DOCKERFILE


def test_the_smoke_test_runs_the_image_read_only_like_the_chart():
    smoke = (ROOT / "scripts" / "smoke-image.sh").read_text(encoding="utf-8")

    assert "--read-only" in smoke
    for path in ("/tmp", "/home/appuser"):
        assert f"--tmpfs {path}" in smoke, path


def test_the_browser_layer_is_finished_before_the_code_is_copied():
    """
    Audit 18.10. final: useradd and chmod -R on the browsers ran after
    COPY src/ - a new layer with Chromium's files on every code change.
    """
    code = DOCKERFILE.index("\nCOPY src/")
    for step in ("useradd", "chmod -R o+rx", INSTALL_BROWSER):
        assert DOCKERFILE.index(step) < code, step
    # One RUN instruction: from "RUN useradd" to the next blank line.
    step = DOCKERFILE[DOCKERFILE.index("RUN useradd") :].split("\n\n", 1)[0]
    assert INSTALL_BROWSER in step and "chmod -R o+rx" in step
    assert step.count("RUN ") == 1


def test_the_image_names_its_version_and_commit():
    for label in (
        "org.opencontainers.image.version",
        "org.opencontainers.image.revision",
    ):
        assert label in DOCKERFILE, label
    assert re.search(r"^ARG VERSION", DOCKERFILE, re.M)
    assert re.search(r"^ARG REVISION", DOCKERFILE, re.M)


def test_both_ci_pass_version_and_commit_to_the_build():
    gitlab = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    github = (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )
    for ci in (gitlab, github):
        for build in re.findall(r"docker (?:buildx )?build[^\n]*", ci):
            assert (
                "--build-arg REVISION=" in build and "--build-arg VERSION=" in build
            ), build


# ------------------------------------------------- audit 14.11.2026


def _instruction(name: str) -> list[str]:
    """The exec form of CMD or ENTRYPOINT as a list."""
    line = re.search(rf"^{name} (\[.*\])$", DOCKERFILE, re.M)
    assert line, name
    return json.loads(line.group(1))


def test_the_listen_address_is_not_fixed_in_the_command():
    """
    O-37: `--host 0.0.0.0` in CMD could not be changed from outside - on an
    IPv6 cluster the API port listened on IPv4 only. uvicorn reads
    UVICORN_HOST; the image sets the default, the chart the cluster's value.
    """
    command = _instruction("CMD")

    assert command[:2] == ["uvicorn", "src.main:app"]
    assert "--host" not in command
    assert re.search(r"^ENV UVICORN_HOST=0\.0\.0\.0$", DOCKERFILE, re.M)
    assert command[command.index("--port") + 1] == "8000"


def test_uvicorn_takes_the_listen_address_from_the_environment(monkeypatch):
    """The image relies on it: without the variable uvicorn listens on 127.0.0.1."""
    from uvicorn.main import main as uvicorn_command

    monkeypatch.setenv("UVICORN_HOST", "::")
    context = uvicorn_command.make_context("uvicorn", _instruction("CMD")[1:])

    assert context.params["host"] == "::"
    assert context.params["port"] == 8000
    assert context.params["timeout_keep_alive"] == 75


def test_keep_alive_outlasts_the_idle_timeout_of_a_proxy():
    """
    O-40: uvicorn closed an idle connection after 5 s, a proxy keeps it for
    60 s (nginx upstream keep-alive) and sent the next request into the closed
    one - sporadic 502.
    """
    command = _instruction("CMD")

    assert int(command[command.index("--timeout-keep-alive") + 1]) > 60


def test_an_init_process_reaps_what_chromium_leaves_behind():
    """O-40: uvicorn as PID 1 does not reap the children of a killed browser."""
    assert _instruction("ENTRYPOINT") == ["tini", "--"]
    packages = DOCKERFILE[DOCKERFILE.index("apt-get install") :].split("&&", 1)[0]
    assert "tini" in packages.split()


def test_only_the_headless_shell_is_installed():
    """
    O-40: `playwright install chromium` downloads Chrome for Testing and the
    headless shell; launch(headless=True), all the service and the smoke test
    use, starts the shell.
    """
    assert INSTALL_BROWSER in DOCKERFILE
    assert not re.search(r"playwright install chromium\b", DOCKERFILE)
    launches = []
    for path in (ROOT / "src/services/screenshot_service.py", ROOT / SMOKE):
        launches += re.findall(r"chromium\.launch\(([^)]*)\)", path.read_text("utf-8"))
    assert launches and all(args == "headless=True" for args in launches), launches


def test_the_smoke_test_checks_readiness_and_the_internal_port():
    """
    /health says the process answers; /ready and the internal port (probes,
    /metrics) are what the cluster uses - an image without them only failed
    after it was published.
    """
    smoke = (ROOT / SMOKE).read_text(encoding="utf-8")

    assert "METADATA_AGENT_INTERNAL_PORT=9000" in smoke
    assert "METADATA_AGENT_REQUIRE_REPOSITORY=false" in smoke
    for url in (":9000/ready", ":9000/metrics", ":8000/ready"):
        assert url in smoke, url
    assert "metadata_agent_" in smoke
    # Inside the container: the internal port is not published.
    assert smoke.count("docker exec") >= 2
