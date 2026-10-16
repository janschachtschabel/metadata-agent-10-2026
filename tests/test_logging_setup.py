"""
Logs go to stdout, as plain text by default or as JSON lines on request.

Team 6 asks for a structured format that can be switched to plain text for
local work, and a log level set by ENV. Plain text stays the default and looks
like before: the message alone, one line each. JSON carries level, logger,
time and the trace id of the request, so log lines can be filtered across
services.
"""

import json
import logging

import pytest

from src.config import Settings
from src.utils.logging_setup import configure_logging
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


def test_json_lines_carry_level_logger_and_trace_id(capsys):
    configure_logging("INFO", "json")

    with traced({"x-client-trace-id": "trace-abc"}):
        logging.getLogger("src.test").warning("⚠️ LLM API returned 503")

    record = json.loads(capsys.readouterr().out)
    assert record["level"] == "WARNING"
    assert record["logger"] == "src.test"
    assert record["message"] == "⚠️ LLM API returned 503"
    assert record["trace_id"] == "trace-abc"
    assert record["time"].endswith("Z")


def test_the_level_filters(capsys):
    configure_logging("WARNING", "text")

    logging.getLogger("src.test").info("not shown")
    logging.getLogger("src.test").error("shown")

    assert capsys.readouterr().out == "shown\n"


def test_json_also_covers_the_server_logs():
    configure_logging("INFO", "json")

    for name in ("uvicorn", "uvicorn.access"):
        handlers = logging.getLogger(name).handlers
        assert handlers and all(
            h.formatter.__class__.__name__ == "JsonFormatter" for h in handlers
        )


def test_http_client_chatter_stays_out():
    # httpx logs every request at INFO - URLs of every LLM and repository call.
    configure_logging("INFO", "text")

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_the_settings_default_to_info_and_text():
    settings = Settings(_env_file=None)

    assert (settings.log_level, settings.log_format) == ("INFO", "text")
