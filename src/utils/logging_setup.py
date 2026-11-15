"""
Where and how the agent logs: stdout, plain text or JSON lines.

METADATA_AGENT_LOG_FORMAT=text (default) writes the message alone, one line
each - what the print() calls used to write. json writes one object per line
with timestamp (UTC), level, service, logger, message and the ids of the
request (trace_id and span_id as in B3, request_id), for log collectors that
filter across services. METADATA_AGENT_LOG_LEVEL sets the threshold. In json
mode uvicorn's own server log uses the same format, so the stream stays
parseable.

uvicorn's access log is off in both formats: log_request writes one line per
request instead - with the duration and the ids, without query string or
headers.
"""

import json
import logging
import re
import sys
from datetime import datetime, timezone

from .request_id import current_trace_ids

# Libraries that log every single HTTP request at INFO.
_QUIET = ("httpx", "httpcore")
_SERVER_LOGGERS = ("uvicorn", "uvicorn.error")
# Polled every few seconds (compose HEALTHCHECK, probes of older deployments).
_PROBE_PATHS = ("/health", "/ready")

# C0 and C1 control characters and DEL, but not tab and line feed.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

_requests = logging.getLogger("metadata_agent.request")


class _StdoutHandler(logging.Handler):
    """Writes to the current sys.stdout - also when it was swapped after setup."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stdout.write(self.format(record) + "\n")
            sys.stdout.flush()
        except Exception:
            self.handleError(record)


class TextFormatter(logging.Formatter):
    """
    The message alone, on one line; a traceback follows on lines of its own.

    Messages carry values from requests (an unknown context, a URL). A line
    break in one of them must not start a line of its own (audit 13.11., S-22),
    and ESC or another control character must not reach a terminal, where it
    recolours or overwrites lines (audit 14.11.). JSON escapes them itself.
    """

    def format(self, record: logging.LogRecord) -> str:
        line = _CONTROL.sub("", " ".join(record.getMessage().splitlines()))
        if record.exc_info:
            line += "\n" + _CONTROL.sub("", self.formatException(record.exc_info))
        return line


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
            # Present on every line, so collectors can rely on the fields.
            "trace_id": None,
            "span_id": None,
            "request_id": None,
            **current_trace_ids(),
        }
        # Parts of the message as fields of their own (log_request). They add
        # to the line and never replace what is above.
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for name, value in fields.items():
                entry.setdefault(name, value)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def log_request(method: str, path: str, status: int, seconds: float) -> None:
    """One line per request; probes only at DEBUG."""
    level = logging.DEBUG if path in _PROBE_PATHS else logging.INFO
    duration_ms = round(seconds * 1000)
    _requests.log(
        level,
        "%s %s %s %s ms",
        method,
        path,
        status,
        duration_ms,
        extra={
            "fields": {
                "method": method,
                "path": path,
                "status": status,
                "duration_ms": duration_ms,
            }
        },
    )


def configure_logging(
    level: str = "INFO", fmt: str = "text", service: str = "metadata-agent"
) -> None:
    """Route all logging to stdout in the chosen format and level."""
    handler = _StdoutHandler()
    handler.setFormatter(JsonFormatter(service) if fmt == "json" else TextFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Python warnings (a deprecation in a library) as log lines of the logger
    # py.warnings instead of unformatted text on stderr (audit 14.11.).
    logging.captureWarnings(True)

    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Without handlers uvicorn does not write its access log at all.
    access = logging.getLogger("uvicorn.access")
    access.handlers[:] = []
    access.propagate = False

    if fmt == "json":
        for name in _SERVER_LOGGERS:
            server_logger = logging.getLogger(name)
            server_logger.handlers[:] = [handler]
            server_logger.propagate = False
