"""Structured logging configuration for the MCP Query Execution server."""

import copy
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.utils.security import SanitizingFormatter


_STANDARD_LOGRECORD_FIELDS = frozenset({
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "getMessage",
    "exc_info",
    "exc_text",
    "stack_info",
    "taskName",
    "timestamp",
    "message",
})


class StructuredFormatter(logging.Formatter):
    """JSON formatter with sensitive-data sanitization.

    Emits one JSON object per record on a single line so that log
    aggregators (Datadog, Loki, CloudWatch, etc.) can parse records
    without custom regex rules.
    """

    def __init__(self) -> None:
        super().__init__()
        self._sanitizer = SanitizingFormatter("%(message)s")

    def format(self, record: logging.LogRecord) -> str:
        sanitized = copy.copy(record)
        if record.args:
            sanitized.args = copy.copy(record.args)
        self._sanitizer.format(sanitized)

        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": sanitized.levelname,
            "logger": sanitized.name,
            "message": sanitized.getMessage(),
        }

        if sanitized.exc_info:
            payload["exception"] = self.formatException(sanitized.exc_info)

        for key, value in sanitized.__dict__.items():
            if key in _STANDARD_LOGRECORD_FIELDS or key.startswith("_"):
                continue
            payload[key] = _coerce_json_safe(value)

        return json.dumps(payload, default=str, ensure_ascii=False)


def _coerce_json_safe(value: Any) -> Any:
    """Best-effort conversion of arbitrary values into JSON-serialisable form."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce_json_safe(v) for k, v in value.items()}
    return repr(value)


def setup_logging(
    level: Optional[str] = None,
    log_file: Optional[Path] = None,
    log_format: Optional[str] = None,
) -> logging.Logger:
    """Configure the application logger.

    Args:
        level: Logging level. Falls back to ``LOG_LEVEL`` env var, then ``INFO``.
        log_file: Optional path to write logs to.
        log_format: ``"structured"`` (JSON) or ``"simple"``. Falls back to
            ``LOG_FORMAT`` env var, then ``"structured"``.

    Returns:
        The configured application logger.
    """
    resolved_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    resolved_format = (log_format or os.getenv("LOG_FORMAT", "structured")).lower()

    try:
        level_int = getattr(logging, resolved_level)
    except AttributeError:
        level_int = logging.INFO

    logger = logging.getLogger("mcp_query_execution")
    logger.setLevel(level_int)
    logger.handlers.clear()

    if resolved_format == "structured":
        formatter: logging.Formatter = StructuredFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level_int)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level_int)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child logger under ``mcp_query_execution``."""
    return logging.getLogger(f"mcp_query_execution.{name}")


default_logger = setup_logging()

