"""
The image is reproducible and the application code is not writable (audit OPS-02).

`chown -R appuser /app` copied the whole virtualenv into another layer and
made the application code writable for the process serving requests; base
images by tag could change under an unchanged Dockerfile.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


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
    for step in ("useradd", "chmod -R o+rx", "playwright install chromium"):
        assert DOCKERFILE.index(step) < code, step
    # One RUN instruction: from "RUN useradd" to the next blank line.
    step = DOCKERFILE[DOCKERFILE.index("RUN useradd") :].split("\n\n", 1)[0]
    assert "playwright install chromium" in step and "chmod -R o+rx" in step
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
