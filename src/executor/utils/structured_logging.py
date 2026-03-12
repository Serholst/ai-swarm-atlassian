"""
Structured logging configuration.

Provides JSON-formatted logging for CI/automation environments
and keeps the default rich-console-friendly format for interactive use.

Usage:
    from executor.utils.structured_logging import setup_structured_logging, new_trace_id, set_trace_id

    trace_id = new_trace_id()          # Generate a new trace ID for a pipeline run
    set_trace_id(trace_id)             # Attach to all subsequent log records
    logger.info("Stage started", extra={"stage": "context_building", "issue_key": "PROJ-123"})
"""

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

# Thread/async-safe trace ID storage
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def new_trace_id() -> str:
    """Generate a new unique trace ID for a pipeline run."""
    return uuid.uuid4().hex[:12]


def set_trace_id(trace_id: str) -> None:
    """Attach a trace ID to the current execution context."""
    _trace_id_var.set(trace_id)


def get_trace_id() -> str:
    """Return the current trace ID (empty string if not set)."""
    return _trace_id_var.get()


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

        # Attach trace ID if present
        trace_id = _trace_id_var.get()
        if trace_id:
            log_entry["trace_id"] = trace_id

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
