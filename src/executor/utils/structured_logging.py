"""
Structured logging configuration.

Provides JSON-formatted logging for CI/automation environments
and keeps the default rich-console-friendly format for interactive use.
"""

import json
import logging
from datetime import datetime, timezone


class StructuredFormatter(logging.Formatter):
    """JSON-structured log formatter for machine-readable output."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
        }

        # Add extra fields if present (set via logger.info("msg", extra={...}))
        for key in ("issue_key", "stage", "duration_ms", "tokens", "step"):
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value

        # Include exception info if present
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False)


def setup_structured_logging(level: str = "INFO", json_output: bool = False) -> None:
    """
    Configure logging for the pipeline.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        json_output: If True, use JSON format; otherwise use simple format
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    if json_output:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter())
        logging.root.handlers = [handler]
        logging.root.setLevel(log_level)
    else:
        # Keep existing simple format for interactive use
        logging.basicConfig(
            level=log_level,
            format="%(message)s",
            force=True,
        )
