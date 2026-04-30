"""Tests for the structured logging formatter."""

import json
import logging

import pytest

from src.utils import logging as app_logging


@pytest.fixture
def record_factory() -> logging.LogRecord:
    """Build a LogRecord suitable for feeding into StructuredFormatter."""

    def _make(
        msg: str = "hello world",
        level: int = logging.INFO,
        name: str = "mcp_query_execution.test",
        args: tuple = (),
        exc_info=None,
        extra: dict | None = None,
    ) -> logging.LogRecord:
        record = logging.LogRecord(
            name=name,
            level=level,
            pathname=__file__,
            lineno=10,
            msg=msg,
            args=args,
            exc_info=exc_info,
        )
        if extra:
            for key, value in extra.items():
                setattr(record, key, value)
        return record

    return _make


class TestStructuredFormatter:
    def test_emits_valid_json(self, record_factory):
        formatter = app_logging.StructuredFormatter()

        output = formatter.format(record_factory("hello"))

        parsed = json.loads(output)
        assert parsed["message"] == "hello"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "mcp_query_execution.test"
        assert "timestamp" in parsed

    def test_includes_exception_when_present(self, record_factory):
        formatter = app_logging.StructuredFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = record_factory(exc_info=sys.exc_info())

        output = formatter.format(record)

        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]
        assert "boom" in parsed["exception"]

    def test_includes_extra_fields(self, record_factory):
        formatter = app_logging.StructuredFormatter()

        output = formatter.format(record_factory(extra={"request_id": "abc-123"}))

        parsed = json.loads(output)
        assert parsed["request_id"] == "abc-123"

    def test_non_serialisable_values_are_coerced(self, record_factory):
        class Custom:
            def __repr__(self) -> str:
                return "<Custom>"

        formatter = app_logging.StructuredFormatter()

        output = formatter.format(record_factory(extra={"obj": Custom()}))

        parsed = json.loads(output)
        assert parsed["obj"] == "<Custom>"

    def test_interpolates_message_args(self, record_factory):
        formatter = app_logging.StructuredFormatter()

        output = formatter.format(record_factory(msg="user %s", args=("alice",)))

        parsed = json.loads(output)
        assert parsed["message"] == "user alice"


class TestSetupLogging:
    def test_respects_log_level_env_var(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "WARNING")

        logger = app_logging.setup_logging()

        assert logger.level == logging.WARNING

    def test_falls_back_to_info_when_unset(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)

        logger = app_logging.setup_logging()

        assert logger.level == logging.INFO

    def test_unknown_level_falls_back_to_info(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "NOPE")

        logger = app_logging.setup_logging()

        assert logger.level == logging.INFO

    def test_simple_format_uses_plain_formatter(self, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "simple")

        logger = app_logging.setup_logging()

        assert logger.handlers
        formatter = logger.handlers[0].formatter
        assert not isinstance(formatter, app_logging.StructuredFormatter)
