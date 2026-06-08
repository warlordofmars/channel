# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the code-exec sandbox Lambda handler (#183).

The handler is a pure Lambda entrypoint — no boto3, no AWS calls. These
tests cover the subprocess driver, length caps, /tmp policy, and image
harvest. Tool-wrapper boto3 / SSE behavior lives in
``test_tools_code_exec.py``."""

from __future__ import annotations


def test_handler_happy_path_returns_stdout_and_exit_zero():
    """``print('hello')`` → stdout = 'hello\\n', exit_code = 0."""
    from channel.sandbox.handler import lambda_handler

    result = lambda_handler({"code": "print('hello')"}, None)

    assert result["stdout"] == "hello\n"
    assert result["stderr"] == ""
    assert result["exit_code"] == 0
    assert result["truncated"] is False
    assert result["timed_out"] is False
    assert result["images"] == []
    assert isinstance(result["duration_ms"], int)
    assert result["duration_ms"] >= 0


def test_handler_non_zero_exit_propagates_without_error_status():
    """``sys.exit(7)`` is a successful tool call returning exit_code=7.

    Non-zero exit is NOT a chassis-level error — the model gets stderr
    and decides what to do. The sandbox's job is to faithfully report
    what the subprocess did."""
    from channel.sandbox.handler import lambda_handler

    result = lambda_handler({"code": "import sys; sys.exit(7)"}, None)

    assert result["exit_code"] == 7
    assert "status" not in result  # not an error result


def test_handler_traceback_in_stderr_propagates():
    """``1/0`` → ZeroDivisionError → traceback in stderr, exit_code != 0."""
    from channel.sandbox.handler import lambda_handler

    result = lambda_handler({"code": "x = 1/0"}, None)

    assert result["exit_code"] != 0
    assert "ZeroDivisionError" in result["stderr"]


def test_handler_timeout_returns_timed_out_with_partial_output(monkeypatch):
    """A subprocess that exceeds the 270 s cap returns:
    ``timed_out=True``, ``exit_code=-1``, partial stdout/stderr if any."""
    from channel.sandbox import handler as handler_module

    # Shorten the cap so the test runs in <1s.
    monkeypatch.setattr(handler_module, "_SUBPROCESS_TIMEOUT_SEC", 0.5)

    code = (
        "import time, sys\n"
        "print('before sleep'); sys.stdout.flush()\n"
        "time.sleep(5)\n"
        "print('after sleep')\n"
    )
    result = handler_module.lambda_handler({"code": code}, None)

    assert result["timed_out"] is True
    assert result["exit_code"] == -1
    assert "before sleep" in result["stdout"]
    assert "after sleep" not in result["stdout"]
