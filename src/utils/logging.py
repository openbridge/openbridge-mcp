"""Structured logging configuration for the MCP Query Execution server."""

import copy
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.utils.security import SanitizingFormatter


class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured logging with sanitization."""

    def __init__(self) -> None:
        super().__init__()
        self._sanitizer = SanitizingFormatter("%(message)s")

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with structured data."""
        sanitized = copy.copy(record)
        if record.args:
            sanitized.args = copy.copy(record.args)
        # Apply sanitization side effects.
        self._sanitizer.format(sanitized)

        sanitized.timestamp = datetime.now(timezone.utc).isoformat()

        structured_data = {
            "timestamp": sanitized.timestamp,
            "level": sanitized.levelname,
            "logger": sanitized.name,
            "message": sanitized.getMessage(),
        }

        if sanitized.exc_info:
            structured_data["exception"] = self.formatException(sanitized.exc_info)

        for key, value in sanitized.__dict__.items():
            if key not in [
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
                "timestamp",
            ]:
                structured_data[key] = value

        return f"{structured_data}"


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    log_format: str = "structured"
) -> logging.Logger:
    """Set up structured logging for the application.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional path to log file
        log_format: Log format ("structured" or "simple")
    
    Returns:
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger("mcp_query_execution")
    logger.setLevel(logging.DEBUG)
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Create formatter
    if log_format == "structured":
        formatter = StructuredFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(getattr(logging, level.upper()))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler (if specified)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(getattr(logging, level.upper()))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the specified name.
    
    Args:
        name: Logger name
    
    Returns:
        Logger instance
    """
    return logging.getLogger(f"mcp_query_execution.{name}")


# Default logger setup - use LOG_LEVEL env var or default to INFO
default_logger = setup_logging(level=os.getenv("LOG_LEVEL", "INFO")) 
