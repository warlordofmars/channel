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


def test_handler_caps_stdout_at_20kb_with_truncation_marker():
    """20 KB stdout cap with a ``…[truncated, N more bytes]`` marker."""
    from channel.sandbox.handler import _STDOUT_CAP, lambda_handler

    # Write 30 KB to stdout
    code = "import sys; sys.stdout.write('x' * 30000)"
    result = lambda_handler({"code": code}, None)

    assert result["truncated"] is True
    # The capped stdout is exactly _STDOUT_CAP bytes including the marker
    assert len(result["stdout"]) <= _STDOUT_CAP
    assert "…[truncated" in result["stdout"]
    assert "more bytes]" in result["stdout"]


def test_handler_caps_stderr_at_5kb_with_truncation_marker():
    """5 KB stderr cap with a ``…[truncated, N more bytes]`` marker."""
    from channel.sandbox.handler import _STDERR_CAP, lambda_handler

    code = "import sys; sys.stderr.write('y' * 10000)"
    result = lambda_handler({"code": code}, None)

    assert result["truncated"] is True
    assert len(result["stderr"]) <= _STDERR_CAP
    assert "…[truncated" in result["stderr"]


def test_handler_no_truncation_below_caps():
    """Output below the caps round-trips verbatim, truncated=False."""
    from channel.sandbox.handler import lambda_handler

    result = lambda_handler({"code": "print('short')"}, None)

    assert result["stdout"] == "short\n"
    assert result["truncated"] is False


def test_handler_wipes_tmp_on_entry(monkeypatch, tmp_path):
    """Pre-seed a file in the sandbox's tmp dir → handler wipes it
    before running user code → user code can't read the prior content."""
    from channel.sandbox import handler as handler_module

    # Redirect the wipe + cwd to a real tmp path we control. Production
    # sets _TMP_DIR = "/tmp"; tests inject a tmp_path-scoped dir so the
    # wipe doesn't touch the real system /tmp.
    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))

    (fake_tmp / "leak.txt").write_text("secret-from-prior-invocation")

    # User code body is just `pass` — the assertion is that the
    # handler wiped fake_tmp BEFORE running the subprocess, which the
    # post-call file check below verifies.
    handler_module.lambda_handler({"code": "pass"}, None)

    assert not (fake_tmp / "leak.txt").exists()
