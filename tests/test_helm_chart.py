"""
The Helm chart and its alert rules against the code they describe.

An alert on a metric or label value that no longer exists never fires, and
nothing else notices. A chart value naming a setting the code does not read
looks configured and does nothing. Both are checked here, without helm or
promtool, so every CI run catches the drift; `helm lint` and
`promtool test rules` (helm/metadata-agent/README.md) check the syntax.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi.routing import iter_route_contexts
from prometheus_client import REGISTRY

import src.metrics  # noqa: F401 - registers the agent's metrics
from src import main
from src.config import INGRESS_TIMEOUT_SECONDS, Settings
from src.metrics import GENERATION_STATUSES, HTTP_DURATION, UPSTREAM_SERVICES
from src.services import llm_errors

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "helm" / "metadata-agent"
PREFIX = "METADATA_AGENT_"

SUFFIXES = {
    "counter": ("_total",),
    "histogram": ("_bucket", "_sum", "_count"),
    "gauge": ("",),
}


def _alert_rules() -> list[dict]:
    groups = yaml.safe_load((CHART / "files" / "alerts.yaml").read_text("utf-8"))
    return [rule for group in groups["groups"] for rule in group["rules"]]


def _values() -> dict:
    return yaml.safe_load((CHART / "values.yaml").read_text("utf-8"))


def _exported_series() -> set[str]:
    names = set()
    for family in REGISTRY.collect():
        for suffix in SUFFIXES.get(family.type, ("",)):
            names.add(family.name + suffix)
    return names


def _label_values(expr: str, label: str) -> set[str]:
    """Values a selector names (=, =~, != or !~); regex values are split on |."""
    values = set()
    for _op, value in re.findall(rf'\b{label}\s*(=~|!=|!~|=)\s*"([^"]*)"', expr):
        values.update(value.split("|"))
    return values


def _matches_one(pattern: str, candidates) -> bool:
    return any(re.fullmatch(pattern, c) for c in candidates)


# ------------------------------------------------------------ alert rules


def test_every_rule_is_complete():
    for rule in _alert_rules():
        assert rule["alert"].startswith("MetadataAgent"), rule
        assert rule["labels"]["severity"] in {"critical", "warning"}, rule
        assert rule["annotations"]["summary"], rule
        assert rule["annotations"]["description"], rule


def test_rules_only_use_metrics_the_agent_exports():
    exported = _exported_series()

    for rule in _alert_rules():
        used = set(re.findall(r"\bmetadata_agent_[a-z_]+", rule["expr"]))
        assert used, f"{rule['alert']} uses no metric of the agent"
        unknown = used - exported
        assert not unknown, f"{rule['alert']}: unbekannte Metriken {unknown}"


def test_llm_result_values_are_real_error_codes():
    codes = {"ok"} | {
        cls.code
        for cls in vars(llm_errors).values()
        if isinstance(cls, type) and issubclass(cls, llm_errors.LLMError)
    }

    for rule in _alert_rules():
        for pattern in _label_values(rule["expr"], "result"):
            assert _matches_one(pattern, codes), f"{rule['alert']}: {pattern}"


def test_key_source_and_config_values_exist():
    """The labels _call_llm sets (tests/test_llm_metric_labels.py)."""
    for rule in _alert_rules():
        for value in _label_values(rule["expr"], "auth_source"):
            assert value in {"forwarded", "static", "openai", "none"}, rule["alert"]
        for value in _label_values(rule["expr"], "config"):
            assert value in {"default", "override"}, rule["alert"]


def test_critical_llm_rules_ignore_the_callers_problems():
    """A caller's key or a model the request picked must never page."""
    for rule in _alert_rules():
        expr = rule["expr"]
        if rule["labels"]["severity"] == "critical" and "llm_requests" in expr:
            assert 'config="default"' in expr, rule["alert"]
            if "LLM_AUTH_" in expr:
                assert 'auth_source!="forwarded"' in expr, rule["alert"]


def test_generation_status_values_exist():
    for rule in _alert_rules():
        for selector in re.findall(
            r"metadata_agent_generations_total\{([^}]*)\}", rule["expr"]
        ):
            for value in _label_values(selector, "status"):
                assert value in GENERATION_STATUSES, rule["alert"]


def test_service_and_route_values_exist():
    # Effective routes: included routers are one entry in app.routes.
    routes = {ctx.path for ctx in iter_route_contexts(main.app.routes)}

    for rule in _alert_rules():
        for pattern in _label_values(rule["expr"], "service"):
            assert _matches_one(pattern, UPSTREAM_SERVICES), (
                f"{rule['alert']}: {pattern}"
            )
        for value in _label_values(rule["expr"], "route"):
            assert value in routes, f"{rule['alert']}: {value}"


def test_upstream_rules_ignore_urls_callers_hand_in():
    """Audit OPS-13: service="other" is a preview URL from a request, not a dependency."""
    for rule in _alert_rules():
        for selector in re.findall(
            r"metadata_agent_upstream_\w+\{([^}]*)\}", rule["expr"]
        ):
            # Either "other" is excluded, or the rule names its services.
            excluded = re.search(r'service\s*(!=|!~)\s*"([^"]*)"', selector)
            named = re.search(r'service\s*=~?\s*"([^"]*)"', selector)
            assert (excluded and "other" in excluded.group(2).split("|")) or (
                named and "other" not in named.group(1).split("|")
            ), (rule["alert"], selector)


def test_critical_llm_rules_see_a_series_that_starts_with_a_failure():
    """
    Audit OPS-06: increase() ignores a series' first value. Besides the series
    created at start (src/metrics.py), each critical rule also reads when the
    agent last failed (audit 14.11., O-34; tests/test_last_failure_gauges.py).
    """
    for rule in _alert_rules():
        if rule["labels"]["severity"] == "critical" and "llm_requests" in rule["expr"]:
            assert (
                "time() - max by (namespace) "
                "(metadata_agent_llm_last_failure_timestamp_seconds{" in rule["expr"]
            ), rule["alert"]


def test_promtool_tests_cover_every_alert():
    tests = (CHART / "tests" / "alerts.test.yaml").read_text("utf-8")

    for rule in _alert_rules():
        assert f"alertname: {rule['alert']}" in tests, rule["alert"]


# ------------------------------------------------------------ chart values


def _configured_variables() -> set[str]:
    return {
        field.alias or f"{PREFIX}{name.upper()}"
        for name, field in Settings.model_fields.items()
    }


def test_chart_values_only_name_settings_the_code_reads():
    values = _values()
    named = set(values["env"]) | set(values["secret"]["keys"])

    stale = sorted(named - _configured_variables())

    assert not stale, f"in values.yaml, aber nicht in Settings: {stale}"


def test_request_body_limit_matches_the_ingress():
    """The ingress must let through what the agent accepts, and no more."""
    values = _values()
    agent_bytes = int(values["env"]["METADATA_AGENT_MAX_REQUEST_BYTES"])
    ingress = values["ingress"]["annotations"][
        "nginx.ingress.kubernetes.io/proxy-body-size"
    ]

    assert ingress == f"{agent_bytes // (1024 * 1024)}m"


def test_chart_version_follows_the_service():
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text("utf-8"))
    project = (ROOT / "pyproject.toml").read_text("utf-8")
    version = re.search(r'^version = "([^"]+)"', project, re.MULTILINE).group(1)

    # One version for service, image and chart: values and image always match
    # (developer guide for services, chapter 4).
    assert chart["version"] == chart["appVersion"] == version
    assert _values()["image"]["tag"] == f"v{version}"


def _value_paths(node, prefix="") -> set[str]:
    """Every value of values.yaml by its path; lists and empty maps are one value."""
    if not isinstance(node, dict) or not node:
        return {prefix}
    paths = set()
    for key, child in node.items():
        paths |= _value_paths(child, f"{prefix}.{key}" if prefix else key)
    return paths


def test_every_chart_value_is_described():
    """`## @param <path> <text>` for each value - and none for a value that is gone."""
    text = (CHART / "values.yaml").read_text("utf-8")
    described = dict(re.findall(r"^\s*## @param (\S+) (\S.*)$", text, re.MULTILINE))

    assert set(described) == _value_paths(_values())


# ------------------------------------------------------------ network policy


def test_the_service_is_isolated_by_default():
    """
    The accepted gaps (no own authorization on /screenshot, /upload with
    node_id, /workflow) rely on only the B-API proxy reaching the service
    (audit S-01). The chart enforces that unless switched off.
    """
    policy = _values()["networkPolicy"]

    assert policy["enabled"] is True
    assert policy["ingressNamespaces"], "without namespaces nobody may call the API"
    assert "10.0.0.0/8" in policy["egressExceptCidrs"]


def test_an_empty_namespace_list_never_renders_an_open_rule():
    """An ingress/egress rule with an empty `from`/`to` allows everything."""
    template = (CHART / "templates" / "networkpolicy.yaml").read_text("utf-8")

    for key in ("ingressNamespaces", "prometheusNamespaces", "egressNamespaces"):
        assert f"if .Values.networkPolicy.{key}" in template, key


def test_pods_do_not_mount_a_service_account_token():
    deployment = (CHART / "templates" / "deployment.yaml").read_text("utf-8")

    assert "automountServiceAccountToken: false" in deployment


# ------------------------------------------------------------ hardening (OPS)


def _deployment() -> str:
    return (CHART / "templates" / "deployment.yaml").read_text("utf-8")


def test_the_root_filesystem_is_read_only_with_writable_scratch_space():
    """Audit OPS-02: only /tmp and the home Chromium writes to are writable."""
    values = _values()
    deployment = _deployment()

    assert values["securityContext"]["readOnlyRootFilesystem"] is True
    for path in ("/tmp", "/home/appuser"):
        assert f"mountPath: {path}" in deployment, path
    assert deployment.count("emptyDir:") >= 2


def test_forwarded_headers_are_trusted_from_the_proxy():
    """
    Audit OPS-01: behind a TLS ingress uvicorn only honours X-Forwarded-Proto
    from FORWARDED_ALLOW_IPS (default 127.0.0.1), so /widget/info answered with
    http:// URLs that browsers block as mixed content. Since the audit of
    14.11. (S-43) /widget/info returns relative references and no answer
    depends on the headers; the chart still sets the variable, and its comment
    must not give the old reason.
    """
    assert "FORWARDED_ALLOW_IPS" in _deployment()
    assert ".Values.forwardedAllowIps" in _deployment()
    assert _values()["forwardedAllowIps"]
    values = (CHART / "values.yaml").read_text("utf-8")
    comment = values[: values.index("## @param forwardedAllowIps")].rsplit("\n\n", 1)[1]
    assert "relative references" in comment
    assert "http://" not in comment


def test_the_internal_port_is_configured_once():
    """Audit OPS-04: the env variable follows service.internalPort, it is not a second value."""
    assert "METADATA_AGENT_INTERNAL_PORT" not in _values()["env"]
    assert ".Values.service.internalPort | quote" in _deployment()


def test_an_old_values_file_cannot_shadow_the_derived_variables():
    """Audit OPS-04: a 0.2.x override with env.METADATA_AGENT_INTERNAL_PORT stops the install."""
    deployment = _deployment()

    for name in ("METADATA_AGENT_INTERNAL_PORT", "FORWARDED_ALLOW_IPS"):
        assert f'"{name}"' in deployment and "fail" in deployment, name


# ------------------------------------------------------------ rendered (helm)
#
# The checks above read the templates as text. These render them where helm is
# installed. Both CI test jobs set REQUIRE_HELM=1 (GitLab copies helm from the
# pinned image of the job "helm binary"): there a missing helm fails the run
# instead of skipping it - until 2026-10-18 GitLab skipped them all.


def _helm() -> str:
    helm = shutil.which("helm")
    if helm is None:
        if os.environ.get("REQUIRE_HELM") == "1":
            pytest.fail("helm not installed (REQUIRE_HELM=1)", pytrace=False)
        pytest.skip("helm not installed")
    return helm


def _template(chart: Path, *sets: str, only: str = "") -> str:
    args = [_helm(), "template", "t", str(chart), "--set", "image.repository=r/i"]
    if only:
        args += ["--show-only", only]
    for value in sets:
        args += ["--set", value]
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def _render(*sets: str) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(_template(CHART, *sets)) if doc]


def _notes(tmp_path: Path, *sets: str) -> str:
    """
    NOTES.txt as helm install prints it. `helm template` skips NOTES and
    `install --dry-run` needs a cluster: render a copy through tpl instead.
    """
    chart = tmp_path / "chart"
    shutil.copytree(CHART, chart)
    shutil.copy(chart / "templates" / "NOTES.txt", chart / "files" / "notes.txt")
    (chart / "templates" / "notes.yaml").write_text(
        'notes: {{ tpl (.Files.Get "files/notes.txt") . | toJson }}\n', "utf-8"
    )
    out = _template(chart, *sets, only="templates/notes.yaml")
    return yaml.safe_load(out)["notes"]


def test_require_helm_turns_a_missing_helm_into_a_failure(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("REQUIRE_HELM", raising=False)
    with pytest.raises(pytest.skip.Exception):
        _helm()

    monkeypatch.setenv("REQUIRE_HELM", "1")
    with pytest.raises(pytest.fail.Exception, match="REQUIRE_HELM"):
        _helm()


def _kind(docs: list[dict], kind: str) -> dict:
    return next(doc for doc in docs if doc["kind"] == kind)


def _pod(docs: list[dict]) -> dict:
    return _kind(docs, "Deployment")["spec"]["template"]["spec"]


def _env(docs: list[dict]) -> dict:
    container = _pod(docs)["containers"][0]
    return {e["name"]: e.get("value") for e in container["env"]}


# OPS-10


def test_dns_egress_reaches_only_the_cluster_dns():
    """A DNS rule without `to` opened port 53 to every address (tunnels)."""
    egress = _kind(_render(), "NetworkPolicy")["spec"]["egress"]
    dns = [
        rule
        for rule in egress
        if {"port": 53, "protocol": "UDP"} in rule.get("ports", [])
    ]

    assert len(dns) == 1
    assert dns[0]["to"] == [
        {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
            },
            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
        }
    ]


def test_dns_can_be_opened_for_node_local_dns_caches():
    egress = _kind(_render("networkPolicy.dns.namespace="), "NetworkPolicy")["spec"][
        "egress"
    ]
    dns = [
        rule
        for rule in egress
        if {"port": 53, "protocol": "UDP"} in rule.get("ports", [])
    ]

    assert "to" not in dns[0]


def test_in_cluster_egress_is_limited_to_the_service_ports():
    """Audit OPS-10: the B-API namespace was open on every port (databases, caches)."""
    values = _values()["networkPolicy"]
    egress = _kind(_render(), "NetworkPolicy")["spec"]["egress"]
    in_cluster = [
        rule
        for rule in egress
        if any(
            "namespaceSelector" in to and "podSelector" not in to
            for to in rule.get("to", [])
        )
    ]

    assert len(in_cluster) == 1
    assert values["egressPorts"]
    assert in_cluster[0]["ports"] == [
        {"port": port, "protocol": "TCP"} for port in values["egressPorts"]
    ]


def test_the_network_caveats_are_documented():
    """IPv6 is never allowed out; a public name that DNATs into the cluster is blocked."""
    values = (CHART / "values.yaml").read_text("utf-8")
    documents = [
        (ROOT / name).read_text("utf-8")
        for name in ("DEPLOYMENT.md", "DEPLOYMENT.de.md")
    ]

    for text in (values, *documents):
        assert "IPv6" in text
        assert "LoadBalancer" in text


# OPS-11


def test_scratch_volumes_have_a_size_limit():
    volumes = {v["name"]: v for v in _pod(_render())["volumes"]}
    values = _values()["scratchVolumes"]

    assert volumes["tmp"]["emptyDir"] == {"sizeLimit": values["tmpSizeLimit"]}
    assert volumes["home"]["emptyDir"] == {"sizeLimit": values["homeSizeLimit"]}


def test_replicas_spread_over_nodes_without_blocking_the_schedule():
    docs = _render()
    constraints = _pod(docs)["topologySpreadConstraints"]
    selector = _kind(docs, "Deployment")["spec"]["selector"]

    assert constraints == [
        {
            "maxSkew": 1,
            "topologyKey": "kubernetes.io/hostname",
            "whenUnsatisfiable": "ScheduleAnyway",
            "labelSelector": selector,
        }
    ]
    assert "topologySpreadConstraints" not in _pod(
        _render("topologySpread.enabled=false")
    )


def test_no_hash_of_the_secret_values_in_the_pod_template():
    """
    Audit OPS-11: an unsalted SHA-256 of the secret values sat in an annotation
    anyone with read access to Deployments sees - a weak WLO password can be
    guessed offline. Secret changes need `kubectl rollout restart` (values.yaml).
    """
    docs = _render("secret.create=true", "secret.values.WLO_GUEST_PASSWORD=geheim")
    annotations = _kind(docs, "Deployment")["spec"]["template"]["metadata"].get(
        "annotations", {}
    )

    assert not any("secret" in key for key in annotations)
    assert "sha256sum" not in _deployment()
    assert "rollout restart" in (CHART / "values.yaml").read_text("utf-8")


# OPS-08


def test_a_shared_llm_limit_is_divided_by_the_replicas():
    """
    AcademicCloud's GWDG limit counts the key, the agent's throttle the pod:
    two pods with the defaults sent twice what the gateway allows.
    """
    env = _env(_render("sharedLlmLimit.enabled=true", "replicaCount=2"))

    assert env["METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS"] == "1"
    assert float(env["METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND"]) == pytest.approx(
        10 / 60 / 2, abs=1e-4
    )


def test_no_shared_llm_limit_by_default():
    env = _env(_render())

    assert "METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS" not in env
    assert "METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND" not in env


def test_the_notes_warn_about_academiccloud_without_a_shared_limit():
    notes = (CHART / "templates" / "NOTES.txt").read_text("utf-8")

    assert "academiccloud" in notes and ".Values.sharedLlmLimit.enabled" in notes


# DOC-05 / OPS-15


def test_values_comments_name_the_generate_timeout():
    """The comments said 90 s after the deadline had moved to 110 s."""
    timeout = _values()["env"]["METADATA_AGENT_GENERATE_TIMEOUT"]
    named = re.findall(
        r"/generate takes up to (\d+) s", (CHART / "values.yaml").read_text("utf-8")
    )

    assert len(named) >= 2
    assert set(named) == {timeout}


def test_ingress_and_grace_period_fit_the_generate_deadline():
    """
    19.10.: /generate may run 300 s. The ingress must wait for its answer, and
    after SIGTERM the pod must live long enough to finish it (deadline + 10 s
    for the answer and preStop, the start warning in Settings).
    """
    values = _values()
    annotations = values["ingress"]["annotations"]
    generate = float(values["env"]["METADATA_AGENT_GENERATE_TIMEOUT"])

    assert generate == Settings(_env_file=None).generate_timeout == 300
    assert annotations["nginx.ingress.kubernetes.io/proxy-read-timeout"] == str(
        INGRESS_TIMEOUT_SECONDS
    )
    assert annotations["nginx.ingress.kubernetes.io/proxy-send-timeout"] == str(
        INGRESS_TIMEOUT_SECONDS
    )
    assert values["terminationGracePeriodSeconds"] == INGRESS_TIMEOUT_SECONDS
    assert generate + 10 <= INGRESS_TIMEOUT_SECONDS


# Review 18.10. #18: an ingress the NetworkPolicy blocks


NETPOL_WARNING = "WARNUNG: Ingress aktiv und NetworkPolicy an"


def test_the_notes_warn_when_the_network_policy_blocks_the_ingress(tmp_path):
    """
    With the defaults the policy admits only the namespace b-api on port 8000;
    the ingress controller's namespace is not in it, so the Ingress answers 504.
    """
    notes = _notes(tmp_path, "ingress.enabled=true")

    assert NETPOL_WARNING in notes
    assert "networkPolicy.ingressNamespaces" in notes
    assert "[b-api]" in notes


@pytest.mark.parametrize(
    "sets",
    [(), ("ingress.enabled=true", "networkPolicy.enabled=false")],
)
def test_no_network_policy_warning_without_the_conflict(tmp_path, sets):
    assert NETPOL_WARNING not in _notes(tmp_path, *sets)


def test_the_ingress_conflict_is_documented_next_to_the_switch():
    values = (CHART / "values.yaml").read_text("utf-8")
    ingress_comment = values[: values.index("ingress:")].rsplit("\n\n", 1)[-1]
    readme = (CHART / "README.md").read_text("utf-8")

    assert "networkPolicy.ingressNamespaces" in ingress_comment
    assert "networkPolicy.ingressNamespaces" in readme


# Review 18.10. nit: replicaCount=0 aborted the rendering (integer divide by zero)


def test_a_shared_llm_limit_renders_with_zero_replicas(tmp_path):
    sets = ("sharedLlmLimit.enabled=true", "replicaCount=0")

    env = _env(_render(*sets))
    notes = _notes(tmp_path, *sets)

    assert env["METADATA_AGENT_LLM_MAX_CONCURRENT_REQUESTS"] == "2"
    assert float(env["METADATA_AGENT_LLM_MAX_REQUESTS_PER_SECOND"]) == pytest.approx(
        10 / 60, abs=1e-4
    )
    assert "Gemeinsames LLM-Limit" in notes


# ------------------------------------------------- audit 13.11.2026, OPS-25


def test_a_number_without_quotes_is_rendered_as_the_number(tmp_path):
    """
    `METADATA_AGENT_MAX_REQUEST_BYTES: 41943040` in a values file of one's own
    reached the pod as "4.194304e+07" (helm reads a YAML number as a float)
    and the service did not start. --set would not show it: it parses ints.
    """
    values = tmp_path / "values.yaml"
    values.write_text(
        yaml.safe_dump(
            {
                "env": {
                    "METADATA_AGENT_MAX_REQUEST_BYTES": 41943040,
                    "METADATA_AGENT_DEBUG_ENDPOINTS": True,
                    "METADATA_AGENT_LLM_TEMPERATURE": 0.3,
                }
            }
        ),
        encoding="utf-8",
    )
    out = subprocess.run(
        [_helm(), "template", "t", str(CHART), "--set", "image.repository=r/i"]
        + ["-f", str(values)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    env = _env([doc for doc in yaml.safe_load_all(out) if doc])

    assert env["METADATA_AGENT_MAX_REQUEST_BYTES"] == "41943040"
    assert env["METADATA_AGENT_DEBUG_ENDPOINTS"] == "true"
    assert env["METADATA_AGENT_LLM_TEMPERATURE"] == "0.3"


def test_a_shared_limit_of_zero_per_minute_is_refused():
    """0 would render a rate of 0 - which the agent reads as "no limit"."""
    with pytest.raises(subprocess.CalledProcessError) as raised:
        _render("sharedLlmLimit.enabled=true", "sharedLlmLimit.requestsPerMinute=0")

    assert "requestsPerMinute" in raised.value.stderr


# ------------------------------------------------- audit 14.11.2026


def _fails(*sets: str) -> str:
    """The message helm stops the rendering with."""
    with pytest.raises(subprocess.CalledProcessError) as raised:
        _render(*sets)
    return raised.value.stderr


def _services(docs: list[dict]) -> dict[str, dict]:
    return {doc["metadata"]["name"]: doc for doc in docs if doc["kind"] == "Service"}


# Alert rules against the metrics (O-33, O-35)


def test_the_slow_generation_rule_reads_a_bucket_the_agent_exports():
    """O-33: a rule on a bucket bound the histogram does not have never fires."""
    (rule,) = [r for r in _alert_rules() if r["alert"] == "MetadataAgentGenerateSlow"]
    patterns = _label_values(rule["expr"], "le") - {"+Inf"}
    # As prometheus_client writes the bounds: "270.0".
    exported = {str(float(bound)) for bound in HTTP_DURATION._upper_bounds}

    assert patterns
    for pattern in patterns:
        assert _matches_one(pattern.replace("\\\\", "\\"), exported), pattern
    assert "histogram_quantile" not in rule["expr"]


def test_unanswered_calls_are_not_counted_per_status_code():
    """O-35: the first answer of a new status code started a series increase() skips."""
    (rule,) = [
        r for r in _alert_rules() if r["alert"] == "MetadataAgentUpstreamUnanswered"
    ]

    assert "metadata_agent_upstream_responses_total" not in rule["expr"]
    assert "metadata_agent_upstream_response_duration_seconds_count" in rule["expr"]


def test_the_failing_generations_rule_counts_only_runs_that_ended():
    (rule,) = [
        r for r in _alert_rules() if r["alert"] == "MetadataAgentGenerationsFailing"
    ]
    selectors = re.findall(
        r"metadata_agent_generations_total(\{[^}]*\})?", rule["expr"]
    )

    assert selectors and all(selectors), "a selector without status counts every run"
    for selector in selectors:
        assert not {"cancelled", "refused"} & _label_values(selector, "status")


def test_the_key_refused_text_fits_direct_calls_too():
    (rule,) = [
        r for r in _alert_rules() if r["alert"] == "MetadataAgentUpstreamKeyRefused"
    ]
    annotations = rule["annotations"]

    assert "B-API" not in annotations["summary"]
    assert "direkt" in annotations["description"]


# O-41


def test_the_version_label_names_the_app_version_not_the_image_tag():
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text("utf-8"))

    for doc in _render("image.tag=develop"):
        labels = doc["metadata"]["labels"]
        assert labels["app.kubernetes.io/version"] == chart["appVersion"], doc["kind"]


def test_an_image_can_be_pinned_by_digest():
    digest = "sha256:" + "0123456789abcdef" * 4
    docs = _render(f"image.digest={digest}")

    assert _pod(docs)["containers"][0]["image"] == f"r/i@{digest}"


def test_without_a_digest_the_image_is_pulled_by_tag():
    image = _pod(_render())["containers"][0]["image"]

    assert image == f"r/i:{_values()['image']['tag']}"
    assert _values()["image"]["digest"] == ""


@pytest.mark.parametrize("digest", ["latest", "sha256:abc", "md5:" + "0" * 64])
def test_a_malformed_digest_is_refused(digest):
    assert "image.digest" in _fails(f"image.digest={digest}")


# O-42


@pytest.mark.parametrize("port", ["0", "70000", "-1", "", "null"])
def test_an_internal_port_that_cannot_be_bound_is_refused(port):
    assert "service.internalPort" in _fails(f"service.internalPort={port}")


def test_a_shared_rate_that_rounds_to_zero_is_refused():
    """0.001 per minute rendered "0.0000" - which the agent reads as "no limit"."""
    message = _fails(
        "sharedLlmLimit.enabled=true", "sharedLlmLimit.requestsPerMinute=0.001"
    )

    assert "requestsPerMinute" in message


def test_the_internal_port_has_a_cluster_ip_service_of_its_own():
    """A LoadBalancer for the API published /metrics and /ready with it."""
    services = _services(_render("service.type=LoadBalancer"))
    public, internal = (
        services["t-metadata-agent"],
        services["t-metadata-agent-internal"],
    )

    assert public["spec"]["type"] == "LoadBalancer"
    assert [port["name"] for port in public["spec"]["ports"]] == ["http"]
    assert internal["spec"]["type"] == "ClusterIP"
    assert [
        (port["name"], port["port"], port["targetPort"])
        for port in internal["spec"]["ports"]
    ] == [("internal", 9000, "internal")]
    assert internal["spec"]["selector"] == public["spec"]["selector"]


def test_the_service_monitor_selects_the_internal_service_only():
    docs = _render()
    services = _services(docs)
    selector = _kind(docs, "ServiceMonitor")["spec"]["selector"]["matchLabels"].items()

    assert (
        selector <= services["t-metadata-agent-internal"]["metadata"]["labels"].items()
    )
    assert not selector <= services["t-metadata-agent"]["metadata"]["labels"].items()
    assert _kind(docs, "ServiceMonitor")["spec"]["endpoints"][0]["port"] == "internal"


# O-37


def test_both_ports_listen_on_ipv4_by_default():
    env = _env(_render())

    assert env["UVICORN_HOST"] == env["METADATA_AGENT_INTERNAL_HOST"] == "0.0.0.0"


def test_one_value_moves_both_ports_to_ipv6():
    env = _env(_render("listenHost=::"))

    assert env["UVICORN_HOST"] == env["METADATA_AGENT_INTERNAL_HOST"] == "::"


@pytest.mark.parametrize("name", ["UVICORN_HOST", "METADATA_AGENT_INTERNAL_HOST"])
def test_a_listen_address_in_env_cannot_split_the_two_ports(name):
    assert "listenHost" in _fails(f"env.{name}=::")


# O-46


def test_the_inbox_is_a_chart_value_with_the_default_of_the_code():
    text = (CHART / "values.yaml").read_text("utf-8")
    inbox = _values()["env"]["METADATA_AGENT_WLO_INBOX_ID"]

    assert inbox == Settings.model_fields["wlo_inbox_id"].default
    comment = text[: text.index("  METADATA_AGENT_WLO_INBOX_ID:")].rsplit("\n\n", 1)[-1]
    assert "target repository" in comment
    assert _env(_render())["METADATA_AGENT_WLO_INBOX_ID"] == inbox


# Small findings (O-N)


@pytest.mark.parametrize(
    "name", ["METADATA_AGENT_B_API_BASE_URL", "METADATA_AGENT_REPOSITORY_URL"]
)
@pytest.mark.parametrize("value", ["", "null"])
def test_an_emptied_address_does_not_fall_back_to_staging(name, value):
    """The chart names production; empty, the agent took the staging default of its code."""
    assert name in _fails(f"env.{name}={value}")


BODY_SIZE = r"ingress.annotations.nginx\.ingress\.kubernetes\.io/proxy-body-size"


def test_a_changed_request_limit_must_change_the_ingress_with_it():
    """Coupled only in the defaults: 40 MB in the agent, 20 MB at the ingress - 413 from nginx."""
    larger = "env.METADATA_AGENT_MAX_REQUEST_BYTES=41943040"

    assert "proxy-body-size" in _fails("ingress.enabled=true", larger)
    docs = _render("ingress.enabled=true", larger, f"{BODY_SIZE}=40m")
    assert (
        _kind(docs, "Ingress")["metadata"]["annotations"][
            "nginx.ingress.kubernetes.io/proxy-body-size"
        ]
        == "40m"
    )
    # Without an ingress there is nothing to keep in step.
    _render(larger)


def test_a_request_limit_that_is_no_whole_megabyte_is_named_in_bytes():
    odd = "env.METADATA_AGENT_MAX_REQUEST_BYTES=30000000"

    assert "30000000" in _fails("ingress.enabled=true", odd)
    _render("ingress.enabled=true", odd, f"{BODY_SIZE}=30000000")


def test_the_agent_is_told_the_grace_period_of_the_chart():
    """Its deadline warnings compared with a fixed 320 s (tests/test_start_warnings.py)."""
    assert _env(_render())["METADATA_AGENT_GRACE_PERIOD_SECONDS"] == "320"
    longer = _env(_render("terminationGracePeriodSeconds=600"))
    assert longer["METADATA_AGENT_GRACE_PERIOD_SECONDS"] == "600"
    assert "METADATA_AGENT_GRACE_PERIOD_SECONDS" in _configured_variables()
    assert "terminationGracePeriodSeconds" in _fails(
        "env.METADATA_AGENT_GRACE_PERIOD_SECONDS=600"
    )
