"""
The Helm chart and its alert rules against the code they describe.

An alert on a metric or label value that no longer exists never fires, and
nothing else notices. A chart value naming a setting the code does not read
looks configured and does nothing. Both are checked here, without helm or
promtool, so every CI run catches the drift; `helm lint` and
`promtool test rules` (helm/metadata-agent/README.md) check the syntax.
"""

import re
from pathlib import Path

import yaml
from prometheus_client import REGISTRY

import src.metrics  # noqa: F401 - registers the agent's metrics
from src import main
from src.config import Settings
from src.metrics import UPSTREAM_SERVICES
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


def test_generation_status_values_exist():
    for rule in _alert_rules():
        for value in _label_values(rule["expr"], "status"):
            if "generations" in rule["expr"]:
                assert value in {"success", "partial", "failed"}, rule["alert"]


def test_service_and_route_values_exist():
    routes = {getattr(route, "path", None) for route in main.app.routes}

    for rule in _alert_rules():
        for pattern in _label_values(rule["expr"], "service"):
            assert _matches_one(pattern, UPSTREAM_SERVICES), (
                f"{rule['alert']}: {pattern}"
            )
        for value in _label_values(rule["expr"], "route"):
            assert value in routes, f"{rule['alert']}: {value}"


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


def test_probes_and_metrics_use_the_internal_port():
    values = _values()

    assert values["env"]["METADATA_AGENT_INTERNAL_PORT"] == str(
        values["service"]["internalPort"]
    )


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

    assert chart["appVersion"] == version
    assert _values()["image"]["tag"] == f"v{version}"
