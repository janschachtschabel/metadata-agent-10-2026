"""
Where and how the agent logs: stdout, plain text or JSON lines.

METADATA_AGENT_LOG_FORMAT=text (default) writes the message alone, one line
each - what the print() calls used to write. json writes one object per line
with time (UTC), level, logger, message and the trace id of the request, for
log collectors that filter across services. METADATA_AGENT_LOG_LEVEL sets the
threshold. In json mode uvicorn's own server and access logs use the same
format, so the stream stays parseable.
"""

import json
import logging
import sys
from datetime import datetime, timezone

from .request_id import current_request_id

# Libraries that log every single HTTP request at INFO.
_QUIET = ("httpx", "httpcore")
_SERVER_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class _StdoutHandler(logging.Handler):
    """Writes to the current sys.stdout - also when it was swapped after setup."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stdout.write(self.format(record) + "\n")
            sys.stdout.flush()
        except Exception:
            self.handleError(record)


class JsonFormatter(logging.Formatter):
    """One JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": current_request_id(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Route all logging to stdout in the chosen format and level."""
    handler = _StdoutHandler()
    handler.setFormatter(
        JsonFormatter() if fmt == "json" else logging.Formatter("%(message)s")
    )

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())

    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)

    if fmt == "json":
        for name in _SERVER_LOGGERS:
            server_logger = logging.getLogger(name)
            server_logger.handlers[:] = [handler]
            server_logger.propagate = False
