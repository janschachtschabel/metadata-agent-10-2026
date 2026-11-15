"""
Logs go to stdout, as plain text by default or as JSON lines on request.

Team 6 asks for a structured format that can be switched to plain text for
local work, and a log level set by ENV. Plain text stays the default and looks
like before: the message alone, one line each. JSON carries timestamp, level,
service, logger, message and the ids of the request (B3 trace and span id,
request id), so log lines can be filtered across services.
"""

import json
import logging

import pytest

from src.config import Settings
from src.utils.logging_setup import configure_logging, log_request
from src.utils.request_id import traced


@pytest.fixture(autouse=True)
def _restore_logging():
    """configure_logging changes process-wide state; put it back afterwards."""
    names = ["", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"]
    saved = {
        n: (
            logging.getLogger(n).handlers[:],
            logging.getLogger(n).level,
            logging.getLogger(n).propagate,
        )
        for n in names
    }
    yield
    for n, (handlers, level, propagate) in saved.items():
        logger = logging.getLogger(n)
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate


def test_plain_text_is_the_message_alone(capsys):
    configure_logging("INFO", "text")

    logging.getLogger("src.test").info("🤖 LLM Service initialized: b-api-openai")

    assert capsys.readouterr().out == "🤖 LLM Service initialized: b-api-openai\n"


def test_json_lines_carry_level_service_logger_and_ids(capsys):
    configure_logging("INFO", "json", "metadata-agent")
    incoming = {
        "x-client-trace-id": "trace-abc",
        "x-b3-traceid": "80f198ee56343ba864fe8b2a57d3eff7",
        "x-b3-spanid": "e457b5a2e4d86bd1",
    }

    with traced(incoming):
        logging.getLogger("src.test").warning("⚠️ LLM API returned 503")

    record = json.loads(capsys.readouterr().out)
    assert record["level"] == "WARNING"
    assert record["service"] == "metadata-agent"
    assert record["logger"] == "src.test"
    assert record["message"] == "⚠️ LLM API returned 503"
    assert record["trace_id"] == "80f198ee56343ba864fe8b2a57d3eff7"
    assert record["span_id"] == "e457b5a2e4d86bd1"
    assert record["request_id"] == "trace-abc"
    assert record["client_trace_id"] == "trace-abc"
    assert record["timestamp"].endswith("Z")


def test_json_lines_outside_a_request_keep_their_shape(capsys):
    configure_logging("INFO", "json", "metadata-agent")

    logging.getLogger("src.test").info("Starting")

    record = json.loads(capsys.readouterr().out)
    assert record["trace_id"] is None and record["span_id"] is None
    assert "client_trace_id" not in record


def test_the_service_name_is_the_configured_one(capsys):
    configure_logging("INFO", "json", "metadata-agent-canvas")

    logging.getLogger("src.test").info("Starting")

    assert json.loads(capsys.readouterr().out)["service"] == "metadata-agent-canvas"


def test_the_level_filters(capsys):
    configure_logging("WARNING", "text")

    logging.getLogger("src.test").info("not shown")
    logging.getLogger("src.test").error("shown")

    assert capsys.readouterr().out == "shown\n"


def test_json_also_covers_the_server_logs():
    configure_logging("INFO", "json")

    for name in ("uvicorn", "uvicorn.error"):
        handlers = logging.getLogger(name).handlers
        assert handlers and all(
            h.formatter.__class__.__name__ == "JsonFormatter" for h in handlers
        )


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_uvicorns_access_log_is_off(fmt):
    # The agent writes its own line per request (log_request); uvicorn skips
    # its access log when the logger has no handlers.
    configure_logging("INFO", fmt)

    assert not logging.getLogger("uvicorn.access").hasHandlers()


# ------------------------------------------------------ one line per request


def test_a_request_is_logged_with_path_status_and_duration(capsys):
    configure_logging("INFO", "text")

    log_request("POST", "/generate", 200, 12.3456)

    assert capsys.readouterr().out == "POST /generate 200 12346 ms\n"


def test_the_request_line_carries_its_parts_as_json_fields(capsys):
    configure_logging("INFO", "json")

    with traced({}):
        log_request("GET", "/schemata", 404, 0.004)

    record = json.loads(capsys.readouterr().out)
    assert (record["method"], record["path"]) == ("GET", "/schemata")
    assert (record["status"], record["duration_ms"]) == (404, 4)
    assert record["trace_id"] and record["span_id"]


@pytest.mark.parametrize("path", ["/health", "/ready"])
def test_probes_are_logged_at_debug_only(capsys, path):
    # A compose HEALTHCHECK every 30 s would otherwise fill the log.
    configure_logging("INFO", "text")

    log_request("GET", path, 200, 0.001)

    assert capsys.readouterr().out == ""


def test_http_client_chatter_stays_out():
    # httpx logs every request at INFO - URLs of every LLM and repository call.
    configure_logging("INFO", "text")

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_the_settings_default_to_info_and_text():
    settings = Settings(_env_file=None)

    assert (settings.log_level, settings.log_format) == ("INFO", "text")


def test_the_service_name_defaults_to_the_charts_name():
    assert Settings(_env_file=None).service_name == "metadata-agent"


def test_extra_fields_cannot_replace_the_base_fields(capsys):
    """Audit 13.11., L-25: `fields` came last and could overwrite message and ids."""
    configure_logging("INFO", "json", "metadata-agent")

    logging.getLogger("src.test").info(
        "the message",
        extra={"fields": {"message": "forged", "level": "X", "path": "/a"}},
    )

    record = json.loads(capsys.readouterr().out)
    assert (record["message"], record["level"]) == ("the message", "INFO")
    assert record["path"] == "/a"


@pytest.mark.parametrize("fields", ["abc", 5, ["a"], None])
def test_fields_that_are_no_mapping_do_not_cost_the_line(capsys, fields):
    configure_logging("INFO", "json", "metadata-agent")

    logging.getLogger("src.test").info("kept", extra={"fields": fields})

    assert json.loads(capsys.readouterr().out)["message"] == "kept"


def test_a_value_json_cannot_write_is_written_as_text(capsys):
    configure_logging("INFO", "json", "metadata-agent")

    logging.getLogger("src.test").info("kept", extra={"fields": {"when": object}})

    assert "object" in json.loads(capsys.readouterr().out)["when"]


# ------------------------------------------------- audit 13.11.2026, S-22


def test_a_message_cannot_start_a_second_line_in_text_format(capsys):
    """
    Values from a request reach log messages (an unknown context, a place
    name). With a line break in them, the text format - the default outside
    the chart - wrote a second line anyone could word.
    """
    configure_logging("INFO", "text")

    logging.getLogger("src.test").warning("Unknown context: x\nERROR forged line\r\n!")

    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert "forged line" in out


def test_a_traceback_keeps_its_lines_in_text_format(capsys):
    configure_logging("INFO", "text")

    try:
        raise ValueError("kaputt")
    except ValueError:
        logging.getLogger("src.test").exception("Fehler bei\nder Arbeit")

    out = capsys.readouterr().out
    first, *rest = out.splitlines()
    assert first == "Fehler bei der Arbeit"
    assert rest[0].startswith("Traceback") and "ValueError: kaputt" in rest[-1]
