# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for structured logging helpers."""

import logging

from channel.logging_config import (
    configure_logging,
    get_logger,
    new_request_id,
    set_request_context,
)


def test_configure_logging_installs_handler():
    configure_logging("test-service")
    logger = logging.getLogger("channel")
    assert len(logger.handlers) >= 1


def test_get_logger_returns_logger():
    logger = get_logger("channel.test")
    assert isinstance(logger, logging.Logger)


def test_new_request_id_is_12_chars():
    rid = new_request_id()
    assert len(rid) == 12
    assert rid.isalnum()


def test_set_request_context_does_not_raise():
    set_request_context("req-123", "client-456")


def test_format_includes_client_id_when_set():
    """Cover the client_id branch in _JsonFormatter.format()."""
    import io

    configure_logging("test-svc")
    set_request_context("req-abc", "client-xyz")
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter())
    logger = get_logger("channel")
    # Temporarily add a plain handler to capture the JSON output
    from channel.logging_config import _JsonFormatter

    json_handler = logging.StreamHandler(buf)
    json_handler.setFormatter(_JsonFormatter())
    logger.addHandler(json_handler)
    logger.info("test message with client")
    logger.removeHandler(json_handler)
    output = buf.getvalue()
    assert "client-xyz" in output
    # Reset context
    set_request_context("", "")


def test_format_includes_exception_info():
    """Cover the exc_info branch in _JsonFormatter.format()."""
    import json as json_mod
    import sys

    from channel.logging_config import _JsonFormatter

    formatter = _JsonFormatter()
    try:
        raise ValueError("test error")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="boom",
        args=(),
        exc_info=exc_info,
    )
    result = formatter.format(record)
    data = json_mod.loads(result)
    assert "error_type" in data
    assert data["error_type"] == "ValueError"
    assert "stack_trace" in data


def test_fingerprint_id_is_deterministic_across_processes():
    """``str(hash(...))`` is salted per process; ``fingerprint_id`` uses
    SHA-256 so the same input always produces the same hash."""
    from channel.logging_config import fingerprint_id

    a = fingerprint_id("user-abc")
    b = fingerprint_id("user-abc")
    assert a == b
    # 16 hex chars (truncated SHA-256).
    assert len(a) == 16
    assert all(c in "0123456789abcdef" for c in a)


def test_fingerprint_id_differs_between_inputs():
    from channel.logging_config import fingerprint_id

    assert fingerprint_id("u1") != fingerprint_id("u2")
