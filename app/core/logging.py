"""Logging setup for the service.

Deployed environments emit one JSON object per line so logs can be
indexed without parsing. Local runs can switch to a plain text format
with ``LOG_JSON=false``. Both formats include the current request id.
"""

import json
import logging
import sys
from datetime import UTC, datetime

from app.core.context import get_request_id

TEXT_LOG_FORMAT = (
    "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"
)


class RequestIdFilter(logging.Filter):
    """Attach the current request id to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Add ``request_id`` to the record and always keep it."""
        record.request_id = get_request_id() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the record serialised as JSON."""
        log_entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


def configure_logging(level: str, use_json: bool) -> None:
    """Configure the root logger and align Uvicorn's loggers with it.

    Args:
        level: Minimum level name to emit, such as ``"INFO"``.
        use_json: Emit JSON lines when True, plain text otherwise.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        JsonFormatter() if use_json else logging.Formatter(TEXT_LOG_FORMAT)
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Route Uvicorn through the root handler so every line has the same
    # format. Its access log is silenced because RequestContextMiddleware
    # already logs each request with its id and duration.
    for logger_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
