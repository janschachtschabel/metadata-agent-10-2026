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
