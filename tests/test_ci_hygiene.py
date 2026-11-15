"""
What both CI systems check before an image is published (audits DEP-02, OPS-03).

- GitHub Actions run by commit SHA: a moved tag would run someone else's code
  next to the DockerHub password.
- Known vulnerabilities in the locked dependencies (pip-audit) and in the image
  (Trivy, critical and fixable) stop the pipeline.
- The built image is started and must answer /health - a merge request that
  breaks the image no longer reaches main unnoticed.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GITHUB = ROOT / ".github" / "workflows" / "docker-publish.yml"
GITLAB = ROOT / ".gitlab-ci.yml"


def _gitlab() -> dict:
    return yaml.safe_load(GITLAB.read_text(encoding="utf-8"))


def _script(job: dict) -> str:
    """The job's script lines; nested lists (YAML anchors) are flattened as GitLab does."""
    lines: list[str] = []
    for entry in job.get("script", []):
        lines.extend(entry if isinstance(entry, list) else [entry])
    return "\n".join(lines)


def test_every_github_action_is_pinned_to_a_commit():
    uses = re.findall(r"uses:\s*(\S+)(.*)", GITHUB.read_text(encoding="utf-8"))

    assert uses, "no actions found"
    for action, rest in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action
        assert re.search(r"#\s*v\d", rest), f"{action}: name the version in a comment"


def test_github_audits_the_locked_dependencies():
    text = GITHUB.read_text(encoding="utf-8")

    assert re.search(
        r"pip-audit==[\d.]+ .*-r requirements\.txt .*--require-hashes", text
    )


SMOKE = "scripts/smoke-image.sh"


def test_the_smoke_test_waits_for_health():
    script = (ROOT / SMOKE).read_text(encoding="utf-8")

    assert "/health" in script and "METADATA_AGENT_B_API_AUTH_MODE=forward" in script


def test_github_smoke_tests_and_scans_the_image_before_publishing():
    text = GITHUB.read_text(encoding="utf-8")
    smoke, scan, push = (
        text.find(SMOKE),
        text.find("aquasec/trivy:"),
        text.find("docker push"),
    )

    assert -1 not in (smoke, scan, push)
    assert smoke < push and scan < push


def test_gitlab_audits_the_locked_dependencies():
    jobs = _gitlab()

    assert any(
        "pip-audit" in _script(job) and "--require-hashes" in _script(job)
        for job in jobs.values()
        if isinstance(job, dict)
    )


def test_gitlab_smoke_tests_and_scans_the_image_in_merge_requests():
    jobs = {name: job for name, job in _gitlab().items() if isinstance(job, dict)}
    mr_jobs = [
        job
        for job in jobs.values()
        if any(
            "merge_request_event" in str(rule.get("if", ""))
            for rule in job.get("rules", [])
        )
    ]

    assert any(
        SMOKE in _script(job) and "aquasec/trivy" in _script(job) for job in mr_jobs
    )


def test_gitlab_has_no_install_job_without_artifacts():
    assert "uv-install" not in _gitlab()


def test_every_github_step_runs_once():
    text = GITHUB.read_text(encoding="utf-8")

    for step in ("Audit dependencies", "Smoke test the image", "Scan the image"):
        assert text.count(f"name: {step}") == 1, step


def test_github_logs_in_only_after_the_image_was_checked():
    """Scanner and smoke test run with the Docker socket; the password must not be on disk yet."""
    text = GITHUB.read_text(encoding="utf-8")

    assert text.find("docker/login-action") > text.find("name: Scan the image")
    assert text.find("docker/login-action") > text.find("name: Smoke test the image")


def test_the_scanner_image_is_pinned_by_digest():
    for ci in (GITHUB, GITLAB):
        images = re.findall(r"aquasec/trivy:\S+", ci.read_text(encoding="utf-8"))
        assert images, ci.name
        for image in images:
            assert re.fullmatch(r"aquasec/trivy:[\d.]+@sha256:[0-9a-f]{64}", image), (
                image
            )


def test_the_smoke_test_starts_chromium_read_only():
    """The one component that writes to disk: launched once in the read-only container."""
    script = (ROOT / SMOKE).read_text(encoding="utf-8")

    assert "docker exec" in script and "chromium.launch" in script


def test_the_smoke_test_cleans_up_even_when_the_start_fails():
    script = (ROOT / SMOKE).read_text(encoding="utf-8")

    assert script.index("trap ") < script.index("docker run")


# ------------------------------------------------- GitLab pipelines (OPS-05/09)


def _resolved(jobs: dict, name: str) -> dict:
    """A job with its `extends` merged in, as GitLab does (deep merge, job wins)."""
    job = jobs[name]
    parents = job.get("extends", [])
    merged: dict = {}
    for parent in [parents] if isinstance(parents, str) else parents:
        merged = _deep_merge(merged, _resolved(jobs, parent))
    return _deep_merge(merged, {k: v for k, v in job.items() if k != "extends"})


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _gitlab_jobs() -> dict[str, dict]:
    """Every runnable job (not hidden, not a keyword), resolved."""
    config = _gitlab()
    keywords = {"stages", "default", "variables", "workflow", "include"}
    return {
        name: _resolved(config, name)
        for name, job in config.items()
        if isinstance(job, dict) and not name.startswith(".") and name not in keywords
    }


def _rule_ifs(job: dict) -> list[str]:
    return [str(rule.get("if", "")) for rule in job.get("rules", [])]


def _scheduled_only(job: dict) -> bool:
    """Every rule that lets the job run asks for a scheduled pipeline."""
    runs = [rule for rule in job.get("rules", []) if rule.get("when") != "never"]
    return bool(runs) and all('"schedule"' in str(rule.get("if", "")) for rule in runs)


def _runs_in_merge_requests(job: dict) -> bool:
    """A job without rules defaults to `only: [branches, tags]` - no MR pipeline."""
    return any("merge_request_event" in cond for cond in _rule_ifs(job))


def test_gitlab_runs_either_a_merge_request_or_a_branch_pipeline():
    """
    Audit OPS-05: without workflow:rules a push to an MR branch starts a branch
    pipeline with the tests and a separate MR pipeline without them - and the MR
    widget only looks at the latter.
    """
    rules = _gitlab()["workflow"]["rules"]
    conditions = [str(rule.get("if", "")) for rule in rules]

    assert "merge_request_event" in conditions[0]
    never = [
        rule
        for rule in rules
        if "$CI_OPEN_MERGE_REQUESTS" in str(rule.get("if", ""))
        and rule.get("when") == "never"
    ]
    assert never, "a branch with an open MR must not start a second pipeline"
    # main and develop publish from their branch pipeline, open MR or not.
    publish_branches = next(
        i for i, cond in enumerate(conditions) if '"develop"' in cond
    )
    assert publish_branches < rules.index(never[0])


def test_every_gitlab_check_runs_in_merge_request_pipelines():
    """Tests, ruff, mypy, pip-audit, chart and alert checks gate the merge (OPS-05)."""
    checks = {
        name: job
        for name, job in _gitlab_jobs().items()
        if job["stage"] != "deploy" and not _scheduled_only(job)
    }

    assert {"run tests", "pip audit", "helm lint", "alert rules"} <= set(checks)
    assert any("ruff check" in _script(job) for job in checks.values())
    assert any("ruff format" in _script(job) for job in checks.values())
    assert any("mypy" in _script(job) for job in checks.values())
    for name, job in checks.items():
        assert _runs_in_merge_requests(job), name


def test_gitlab_publishes_only_from_main_develop_and_tags():
    publish = {
        name: job
        for name, job in _gitlab_jobs().items()
        if "docker image push" in _script(job)
    }

    assert len(publish) == 2
    for name, job in publish.items():
        assert job["stage"] == "deploy", name
        assert not _runs_in_merge_requests(job), name
        conditions = " ".join(_rule_ifs(job))
        assert "'main'" in conditions or "CI_COMMIT_TAG" in conditions, name


def test_gitlab_logs_in_directly_before_the_push():
    """
    Audit OPS-09: build, smoke test and scanner run with the Docker socket; the
    registry password must not be in the daemon's config while they run.
    """
    for name, job in _gitlab_jobs().items():
        lines = _script(job).splitlines()
        if not any("docker image push" in line for line in lines):
            continue
        login = next(i for i, line in enumerate(lines) if "docker login" in line)
        push = next(i for i, line in enumerate(lines) if "docker image push" in line)
        assert login == push - 1, name
        assert all("docker login" not in line for line in lines[:login]), name


def test_every_gitlab_job_names_its_image():
    """Audit OPS-09: so the default image (and the empty .pre stage) can go."""
    config = _gitlab()

    assert "default" not in config
    assert ".pre" not in config["stages"]
    for name, job in _gitlab_jobs().items():
        assert job.get("image"), name


def _image(job: dict) -> str:
    image = job["image"]
    return image["name"] if isinstance(image, dict) else image


def test_chart_tool_images_are_pinned_by_digest():
    """helm, promtool and kubeconform see the chart; a moved tag must not change them."""
    digest = r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}"
    tool_images = [
        _image(job)
        for job in _gitlab_jobs().values()
        if any(tool in _image(job) for tool in ("helm", "prometheus"))
    ]

    assert len(tool_images) >= 2
    for image in tool_images:
        assert re.fullmatch(digest, image), image
    for image in re.findall(
        r"(?:prom/prometheus|ghcr\.io/yannh/kubeconform):\S+",
        GITHUB.read_text(encoding="utf-8"),
    ):
        assert re.fullmatch(digest, image), image


def test_github_token_is_read_only():
    """Audit OPS-09: the workflow only reads the repository."""
    workflow = yaml.safe_load(GITHUB.read_text(encoding="utf-8"))

    assert workflow["permissions"] == {"contents": "read"}


def test_both_ci_validate_the_rendered_chart_with_kubeconform():
    """Audit T-07: helm lint does not check the manifests against the Kubernetes schemas."""
    jobs = _gitlab_jobs()
    kubeconform = [job for job in jobs.values() if "kubeconform" in _script(job)]

    assert kubeconform and all(_runs_in_merge_requests(j) for j in kubeconform)
    rendered = " ".join(_script(job) for job in jobs.values())
    assert "ingress.enabled=true" in rendered
    assert "kubeconform" in GITHUB.read_text(encoding="utf-8")


def test_both_ci_require_the_browser_tests():
    """Audit T-03: a missing Chromium must fail CI, not skip the browser tests."""
    run_tests = _gitlab_jobs()["run tests"]

    assert str(run_tests["variables"]["REQUIRE_BROWSER"]) == "1"
    workflow = yaml.safe_load(GITHUB.read_text(encoding="utf-8"))
    assert str(workflow["jobs"]["test"]["env"]["REQUIRE_BROWSER"]) == "1"


def test_both_ci_render_the_chart_in_the_tests():
    """
    Review 18.10. #21: the rendered-chart tests skip without helm, and the
    GitLab test image has none - they skipped there on every run. GitLab copies
    helm out of the pinned helm image in an earlier stage; REQUIRE_HELM=1 turns
    a missing helm into a failure.
    """
    jobs = _gitlab_jobs()
    run_tests = jobs["run tests"]
    stages = _gitlab()["stages"]
    providers = {
        name: job
        for name, job in jobs.items()
        if "tools/helm" in _script(job) and "helm" in _image(job)
    }

    assert str(run_tests["variables"]["REQUIRE_HELM"]) == "1"
    assert len(providers) == 1
    ((name, provider),) = providers.items()
    assert stages.index(provider["stage"]) < stages.index(run_tests["stage"])
    assert "tools/helm" in provider["artifacts"]["paths"]
    assert name in run_tests["dependencies"]
    assert _runs_in_merge_requests(provider)
    assert "$CI_PROJECT_DIR/tools" in _script(run_tests)
    workflow = yaml.safe_load(GITHUB.read_text(encoding="utf-8"))
    assert str(workflow["jobs"]["test"]["env"]["REQUIRE_HELM"]) == "1"


# ------------------------------------------------- audit 18.10. final (CI)


def _dockerfile_base() -> str:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    return re.findall(r"^FROM (\S+)", dockerfile, re.M)[-1]


def _without_digest(image: str) -> str:
    return image.split("@", 1)[0]


def test_gitlab_tests_run_on_the_os_of_the_image():
    """
    The image is Debian 13 (trixie); the tests ran on bookworm.

    Same image and tag, each pinned by digest - but not the same digest:
    Dependabot refreshes the Dockerfile digest weekly and cannot touch
    .gitlab-ci.yml, so an equal-digest rule failed every one of its pull
    requests (GitHub, 18.10.).
    """
    jobs = _gitlab_jobs()
    variables = _gitlab()["variables"]

    for name in ("run tests", "pip audit", "pip audit dev"):
        image = _image(jobs[name]).replace(
            "$CI_PYTHON_IMAGE", variables["CI_PYTHON_IMAGE"]
        )
        assert _without_digest(image) == _without_digest(_dockerfile_base()), name
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), name
        assert "pip install uv==$UV_VERSION" in _script(jobs[name]), name


def test_both_ci_report_the_age_of_the_bundled_browser():
    assert "scripts/check_browser_age.py" in _script(_gitlab_jobs()["run tests"])
    assert "scripts/check_browser_age.py" in GITHUB.read_text(encoding="utf-8")


def test_both_ci_audit_the_dev_dependencies_without_blocking():
    job = _gitlab_jobs()["pip audit dev"]

    assert job.get("allow_failure") is True
    assert "--only-dev" in _script(job) and "pip-audit" in _script(job)
    steps = yaml.safe_load(GITHUB.read_text(encoding="utf-8"))["jobs"]["test"]["steps"]
    dev = [s for s in steps if "--only-dev" in s.get("run", "")]
    assert dev and dev[0].get("continue-on-error") is True


def test_the_crd_catalog_is_pinned_to_a_commit():
    for text in (
        GITLAB.read_text(encoding="utf-8"),
        GITHUB.read_text(encoding="utf-8"),
    ):
        urls = re.findall(
            r"https://raw\.githubusercontent\.com/datreeio/CRDs-catalog/([^/]+)/", text
        )
        assert urls, "no CRD catalog"
        for ref in urls:
            assert re.fullmatch(r"[0-9a-f]{40}", ref), ref


def test_gitlab_publishes_only_version_tags():
    job = _gitlab_jobs()["build and push docker image on tag creation"]

    assert any("$CI_COMMIT_TAG =~ /^v" in condition for condition in _rule_ifs(job))


def test_dependency_updates_are_proposed_automatically():
    """
    Audit 18.10. final: nothing proposed updates - the bundled Chromium aged
    unseen. Dependabot covers the lock (playwright, i.e. Chromium), the base
    images by digest and the pinned actions.
    """
    config = yaml.safe_load(
        (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    ecosystems = {entry["package-ecosystem"] for entry in config["updates"]}

    assert {"uv", "docker", "github-actions"} <= ecosystems


def test_dependabot_keeps_the_python_version_of_the_image():
    """
    Dependabot proposed python:3.13-slim -> 3.14-slim (GitHub, 18.10.): it only
    edits the Dockerfile, while the CI images and the tests stay on 3.13. A new
    Python version is a deliberate change across Dockerfile and both CIs;
    Dependabot proposes digests (security fixes) of 3.13 only.
    """
    config = yaml.safe_load(
        (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    (docker,) = [e for e in config["updates"] if e["package-ecosystem"] == "docker"]
    ignored = {
        rule["dependency-name"]: set(rule.get("update-types", []))
        for rule in docker.get("ignore", [])
    }

    assert {
        "version-update:semver-major",
        "version-update:semver-minor",
    } <= ignored.get("python", set())


def test_dependabot_waits_a_week_for_new_releases():
    """
    Its first pull request proposed playwright 1.64.0, five days old: a
    release that new had been passed over on purpose (supply chain).
    """
    config = yaml.safe_load(
        (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )

    for entry in config["updates"]:
        assert entry.get("cooldown", {}).get("default-days", 0) >= 7, entry[
            "package-ecosystem"
        ]


def test_the_ruff_rule_set_does_not_follow_ruff_releases():
    """
    ruff 0.17 widened its default rules: Dependabot's update turned a clean
    tree into 591 findings (GitHub, 18.10.). The project names its rules.
    """
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ruff = pyproject["tool"]["ruff"]

    assert set(ruff["lint"]["select"]) == {"E4", "E7", "E9", "F", "C901"}
    assert "extend-select" not in ruff["lint"]
    # 0.17 also formats the Python blocks of Markdown files (README).
    assert ruff["include"] == ["*.py", "*.pyi", "**/pyproject.toml"]


# ------------------------------------------------- audit 13.11.2026


def test_gitlab_lints_with_the_ruff_of_the_lock_file():
    """
    OPS-23: the job said "Same ruff as uv.lock" and ran 0.15.5 against a lock
    file with 0.17.0 - `ruff format` may judge differently between versions.
    """
    lock = (GITLAB.parent / "uv.lock").read_text(encoding="utf-8")
    locked = re.search(r'name = "ruff"\nversion = "([^"]+)"', lock).group(1)
    image = yaml.safe_load(GITLAB.read_text(encoding="utf-8"))[".base_ruff"]["image"][
        "name"
    ]

    assert re.fullmatch(
        rf"ghcr\.io/astral-sh/ruff:{re.escape(locked)}-alpine@sha256:[0-9a-f]{{64}}",
        image,
    ), image


def test_kubeconform_is_pinned_by_digest_in_gitlab_too():
    image = _image(_gitlab_jobs()["kubeconform"])

    assert re.fullmatch(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}", image), image


def test_a_release_tag_must_match_the_version_it_ships():
    """
    OPS-22: any tag starting with "v" published an image, whatever version the
    code named. A tag without the version bump gives an image that reports
    another version and has no chart of its own.
    """
    job = _gitlab_jobs()["build and push docker image on tag creation"]
    script = _script(job)

    assert any(r"/^v\d+\.\d+\.\d+$/" in condition for condition in _rule_ifs(job)), (
        _rule_ifs(job)
    )
    assert "pyproject.toml" in script
    assert script.index("pyproject.toml") < script.index("docker buildx build")

    workflow = GITHUB.read_text(encoding="utf-8")
    check = workflow.index("Tag matches the version")
    assert check < workflow.index("Publish to DockerHub")


def test_github_publishes_only_main_and_tags():
    """OPS-22: workflow_dispatch pushed any branch to DockerHub."""
    workflow = GITHUB.read_text(encoding="utf-8")
    publish = re.search(r"PUBLISH: \$\{\{(.*?)\}\}", workflow, re.DOTALL).group(1)

    assert "refs/heads/main" in publish and "refs/tags/v" in publish


# ------------------------------------------------- audit 14.11.2026 (O-32, O-43)

SCHEDULED = ROOT / ".github" / "workflows" / "scheduled-checks.yml"
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
PINNED_IMAGE = r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}"


def _scheduled_gitlab_jobs() -> dict[str, dict]:
    return {name: job for name, job in _gitlab_jobs().items() if _scheduled_only(job)}


def _scheduled_gitlab_script() -> str:
    return "\n".join(_script(job) for job in _scheduled_gitlab_jobs().values())


def test_every_action_of_every_workflow_is_pinned_to_a_commit():
    assert SCHEDULED in WORKFLOWS
    for workflow in WORKFLOWS:
        uses = re.findall(r"uses:\s*(\S+)(.*)", workflow.read_text(encoding="utf-8"))
        assert uses, workflow.name
        for action, rest in uses:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action
            assert re.search(r"#\s*v\d", rest), action


def test_every_workflow_only_reads_the_repository():
    for workflow in WORKFLOWS:
        config = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        assert config["permissions"] == {"contents": "read"}, workflow.name


def test_a_published_image_is_scanned_again_on_a_schedule():
    """
    O-32: an image was scanned once, before it was published. A vulnerability
    found afterwards in what it ships was never reported.
    """
    scheduled = yaml.safe_load(SCHEDULED.read_text(encoding="utf-8"))
    # PyYAML reads the key `on` as the boolean True.
    assert "schedule" in scheduled[True]
    assert _scheduled_gitlab_jobs(), "no GitLab job for scheduled pipelines"
    for text in (_scheduled_gitlab_script(), SCHEDULED.read_text(encoding="utf-8")):
        scans = [
            line
            for line in text.splitlines()
            if "--exit-code 1" in line or "--severity" in line
        ]
        assert any("HIGH,CRITICAL" in line for line in scans), scans
        # The newest release tag, not the branch the schedule runs on.
        assert "git ls-remote --tags" in text and "--sort=-v:refname" in text
        assert "pip-audit" in text and "--require-hashes" in text
        assert "scripts/check_browser_age.py" in text


def test_the_scheduled_scanner_is_pinned_like_the_one_before_publishing():
    def scanners(text: str) -> set[str]:
        return set(re.findall(r"aquasec/trivy:\S+", text))

    before = scanners(GITHUB.read_text(encoding="utf-8"))
    assert len(before) == 1
    for text in (GITLAB.read_text(encoding="utf-8"), SCHEDULED.read_text("utf-8")):
        assert scanners(text) == before


def test_a_scheduled_pipeline_neither_publishes_nor_repeats_the_checks():
    """On main a schedule would otherwise rebuild and push the image "main"."""
    assert any(
        '"schedule"' in str(rule.get("if", ""))
        for rule in _gitlab()["workflow"]["rules"]
    )
    for name, job in _gitlab_jobs().items():
        if _scheduled_only(job):
            assert "docker image push" not in _script(job), name
            continue
        if all("merge_request_event" in condition for condition in _rule_ifs(job)):
            continue  # a schedule is no merge request
        first = job["rules"][0]
        assert '"schedule"' in str(first.get("if", "")), name
        assert first.get("when") == "never", name


def test_the_browser_age_must_be_known_in_ci():
    """O-32: the check passed whenever it could not check."""
    jobs = _gitlab_jobs()
    with_check = [
        job for job in jobs.values() if "scripts/check_browser_age.py" in _script(job)
    ]

    assert len(with_check) >= 2
    for job in with_check:
        assert str(job["variables"]["REQUIRE_BROWSER_AGE"]) == "1"
    for workflow in WORKFLOWS:
        config = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for name, job in config["jobs"].items():
            runs = " ".join(step.get("run", "") for step in job["steps"])
            if "scripts/check_browser_age.py" in runs:
                assert str(job["env"]["REQUIRE_BROWSER_AGE"]) == "1", name


def test_a_software_bill_of_materials_is_kept_for_released_images():
    """O-32: what a published image contains could only be found out by pulling it."""
    sbom = "--format cyclonedx"
    with_sbom = {
        name: job for name, job in _gitlab_jobs().items() if sbom in _script(job)
    }

    assert "build and push docker image on tag creation" in with_sbom
    assert set(with_sbom) & set(_scheduled_gitlab_jobs())
    for name, job in with_sbom.items():
        assert "sbom.cdx.json" in job["artifacts"]["paths"], name
    assert sbom in SCHEDULED.read_text(encoding="utf-8")


def test_one_uv_version_in_image_and_both_ci():
    """
    O-43: the pin had three copies and nothing held them together - an update
    of one (Dependabot edits the Dockerfile only) left CI and image resolving
    with different versions.
    """
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    (image,) = re.findall(r"ghcr\.io/astral-sh/uv:([\d.]+)@sha256", dockerfile)

    assert str(_gitlab()["variables"]["UV_VERSION"]) == image
    for workflow in WORKFLOWS:
        installs = re.findall(r"pip install uv==(\S+)", workflow.read_text("utf-8"))
        assert installs and set(installs) == {image}, workflow.name


def test_github_names_the_python_patch_version_it_tests_with():
    """uv picked the newest Python it knew: the version moved with the uv pin."""
    for workflow in WORKFLOWS:
        config = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for name, job in config["jobs"].items():
            runs = " ".join(step.get("run", "") for step in job["steps"])
            if "uv sync" in runs or "uvx " in runs:
                assert re.fullmatch(r"3\.13\.\d+", str(job["env"]["UV_PYTHON"])), name


def test_github_takes_helm_from_the_image_gitlab_pins():
    """The runner image's helm moves with the runner; the tests render with it."""
    helm_images = {
        _image(job) for job in _gitlab_jobs().values() if "alpine/helm" in _image(job)
    }
    assert len(helm_images) == 1
    (image,) = helm_images
    assert re.fullmatch(PINNED_IMAGE, image)

    config = yaml.safe_load(GITHUB.read_text(encoding="utf-8"))
    for name in ("test", "chart"):
        steps = config["jobs"][name]["steps"]
        runs = [step.get("run", "") for step in steps]
        provider = next(i for i, run in enumerate(runs) if image in run)
        assert "$GITHUB_PATH" in runs[provider], name
        users = [
            i
            for i, run in enumerate(runs)
            if re.search(r"\bhelm (lint|template)\b|pytest", run)
        ]
        assert users and min(users) > provider, name


def test_kubeconform_validates_against_a_named_kubernetes_version():
    """Without it the schemas of the newest Kubernetes are used - they move."""
    versions = []
    for text in (GITLAB.read_text("utf-8"), GITHUB.read_text("utf-8")):
        calls = [
            line for line in text.splitlines() if "-schema-location default" in line
        ]
        assert calls
        for call in calls:
            (version,) = re.findall(r"-kubernetes-version (\S+)", call)
            versions.append(version)
    assert len(set(versions)) == 1
    assert re.fullmatch(r"\d+\.\d+\.\d+", versions[0])
