"""
Small findings of the audit of 14.11. in the app, its log and its metrics.

- S-43: `/generate/` was answered with a 307 whose address came from the Host
  header of the request. The app no longer redirects for a trailing slash.
- A page on another origin could not read `Retry-After` of a 429 or 503: the
  header was not among the exposed ones.
- The text log removed line breaks from a message but passed ESC on - a value
  from a request could recolour or overwrite lines in a terminal.
- Python warnings went to stderr in their own format; they now take the log's.
- An internal address the socket cannot even be created for (IPv6 on a host
  without it) ended the start with a bare OSError (O-37).
- `service="other"` (any URL a caller hands in) carried every status code a
  foreign server invents; it now carries the status class.
- /generate runs are counted with one of a fixed set of statuses, never
  "unknown"; a cancelled or refused run has a status of its own.
"""

import json
import logging
import socket
import warnings

import httpx
import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from src import infra, main, metrics
from src.utils.logging_setup import TextFormatter, configure_logging

# ------------------------------------------------------------ S-43


@pytest.mark.parametrize("path", ["/generate/", "/widget/info/", "/widget/examples"])
def test_no_redirect_is_built_from_the_host_header(path):
    with TestClient(main.app) as client:
        response = client.request(
            "POST" if path.startswith("/generate") else "GET",
            path,
            headers={"Host": "evil.example"},
            follow_redirects=False,
        )

    assert response.status_code == 404
    assert "location" not in response.headers


def test_the_routes_themselves_still_answer():
    with TestClient(main.app) as client:
        assert client.get("/widget/info").status_code == 200
        assert client.get("/widget/examples/default.html").status_code == 200


# ------------------------------------------------------------ CORS


def test_a_page_on_another_origin_can_read_retry_after():
    with TestClient(main.app) as client:
        response = client.get(
            "/widget/info", headers={"Origin": "https://host.example"}
        )

    exposed = response.headers["access-control-expose-headers"].lower()
    assert "retry-after" in exposed and "x-request-id" in exposed


# ------------------------------------------------------------ log


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("src.test", logging.INFO, __file__, 1, message, None, None)


@pytest.mark.parametrize(
    "message, line",
    [
        ("context \x1b[31mred\x1b[0m", "context [31mred[0m"),
        ("bell\x07 back\x08 null\x00 del\x7f csi\x9b2J", "bell back null del csi2J"),
        # Line breaks become blanks, as before; a tab stays.
        ("first\r\nsecond\tcolumn", "first second\tcolumn"),
        ("Erschließung – fertig ✓", "Erschließung – fertig ✓"),
    ],
)
def test_the_text_log_passes_no_control_characters_on(message, line):
    assert TextFormatter().format(_record(message)) == line


def test_a_traceback_in_the_text_log_keeps_its_lines_but_no_escape():
    try:
        raise ValueError("value \x1b[2J from a request")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "src.test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )

    text = TextFormatter().format(record)

    assert "\x1b" not in text
    assert text.splitlines()[0] == "failed"
    assert text.splitlines()[-1] == "ValueError: value [2J from a request"


@pytest.fixture
def _logging_restored():
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    # Whatever an earlier import of src.main installed: start from "not captured".
    logging.captureWarnings(False)
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        yield
        logging.captureWarnings(False)
    root.handlers[:], level = saved[0], saved[1]
    root.setLevel(level)


def test_python_warnings_take_the_format_of_the_log(_logging_restored, capsys):
    configure_logging("INFO", "json", "metadata-agent")

    warnings.warn("something is deprecated", DeprecationWarning)

    line = json.loads(capsys.readouterr().out)
    assert line["logger"] == "py.warnings"
    assert line["level"] == "WARNING"
    assert "something is deprecated" in line["message"]


# ------------------------------------------------------------ internal port


def test_an_address_family_the_host_lacks_is_reported_as_unavailable(monkeypatch):
    def no_ipv6(family, *args, **kwargs):
        raise OSError(97, "Address family not supported by protocol")

    monkeypatch.setattr(infra.socket, "socket", no_ipv6)

    with pytest.raises(infra.InternalPortUnavailable, match="Address family"):
        infra._bind("::", 9000)


def test_an_ipv6_address_is_bound_with_an_ipv6_socket(monkeypatch):
    families = []
    real = socket.socket

    def recording(family, *args, **kwargs):
        families.append(family)
        return real(socket.AF_INET, *args, **kwargs)

    monkeypatch.setattr(infra.socket, "socket", recording)

    # The IPv4 socket cannot bind "::" - which is the unavailable case again.
    with pytest.raises(infra.InternalPortUnavailable):
        infra._bind("::", 0)

    assert families == [socket.AF_INET6]


# ------------------------------------------------------------ metrics


def _sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def _answer(url: str, status: int) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    async with httpx.AsyncClient(
        transport=transport, event_hooks=metrics.THIRD_PARTY_HOOKS
    ) as client:
        await client.get(url)


@pytest.mark.parametrize("status, label", [(200, "2xx"), (418, "4xx"), (599, "5xx")])
async def test_a_foreign_server_adds_no_status_label_of_its_own(status, label):
    name = "metadata_agent_upstream_responses_total"
    before = _sample(name, service="other", status=label)

    await _answer("https://somewhere.example/preview.png", status)

    assert _sample(name, service="other", status=label) == before + 1
    assert _sample(name, service="other", status=str(status)) == 0.0


async def test_the_agents_own_services_keep_the_exact_status():
    name = "metadata_agent_upstream_responses_total"
    before = _sample(name, service="geocoder", status="403")

    await _answer("https://photon.komoot.io/api?q=Weimar", 403)

    assert _sample(name, service="geocoder", status="403") == before + 1


def test_the_duration_histogram_can_tell_150_s_from_270_s():
    """O-33: between 120 and 300 s there was no bucket."""
    bounds = metrics.HTTP_DURATION._upper_bounds

    assert {150, 180, 210, 240, 270} <= set(bounds)
    assert bounds == sorted(bounds)


@pytest.mark.parametrize("status", metrics.GENERATION_STATUSES)
def test_a_generation_is_counted_with_its_status(status):
    name = "metadata_agent_generations_total"
    before = _sample(name, status=status)

    metrics.count_generation(status)

    assert _sample(name, status=status) == before + 1


def test_the_generation_statuses_are_the_ones_the_alerts_expect():
    assert set(metrics.GENERATION_STATUSES) == {
        "success",
        "partial",
        "failed",
        "cancelled",
        "refused",
    }


@pytest.mark.parametrize("status", [None, "", "unknown", "something-else"])
def test_a_run_without_a_known_status_is_counted_as_failed(status):
    """Never a label of its own: the alert on failing runs would not see it."""
    name = "metadata_agent_generations_total"
    failed = _sample(name, status="failed")

    metrics.count_generation(status)

    assert _sample(name, status="failed") == failed + 1
    assert _sample(name, status="unknown") == 0.0


def test_a_cancelled_llm_call_has_a_result_of_its_own():
    """L-36: the transport counts it as result="cancelled"; no alert selects it."""
    name = "metadata_agent_llm_requests_total"
    labels = dict(
        provider="b-api-openai",
        result="cancelled",
        auth_source="static",
        config="default",
    )
    before = _sample(name, **labels)

    metrics.LLM_REQUESTS.labels(**labels).inc()

    assert _sample(name, **labels) == before + 1
    assert "cancelled" not in metrics.CRITICAL_LLM_RESULTS
