# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the code-exec sandbox Lambda handler (#183).

The handler is a pure Lambda entrypoint — no boto3, no AWS calls. These
tests cover the subprocess driver, length caps, /tmp policy, and image
harvest. Tool-wrapper boto3 / SSE behavior lives in
``test_tools_code_exec.py``."""

from __future__ import annotations

import os


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


def test_wipe_tmp_early_returns_when_dir_missing(monkeypatch):
    """If ``_TMP_DIR`` doesn't exist, ``_wipe_tmp`` returns immediately
    without raising. Covers the defensive early-return guard."""
    from channel.sandbox import handler as handler_module

    monkeypatch.setattr(
        handler_module, "_TMP_DIR", "/nonexistent-path-for-test-12345"
    )

    # Should return cleanly with no side effects.
    handler_module._wipe_tmp()


def test_wipe_tmp_swallows_oserror_on_unlink(monkeypatch, tmp_path):
    """If ``os.unlink`` raises OSError on a file, ``_wipe_tmp`` swallows
    the error so a permission issue on one file doesn't fail the whole
    invocation. Covers the ``except OSError: pass`` branch."""
    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    (fake_tmp / "stubborn.txt").write_text("can't delete me")
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))

    # Force os.unlink to raise OSError for every file. Test passes if
    # the exception is swallowed and _wipe_tmp returns cleanly.
    def boom(_path):
        raise OSError("simulated permission denied")
    monkeypatch.setattr(handler_module.os, "unlink", boom)

    handler_module._wipe_tmp()  # must not raise


def test_handler_timeout_with_no_stdout_returns_empty_string(monkeypatch):
    """When the subprocess times out without producing any stdout (the
    code blocks before any output), ``exc.stdout`` is None. Handler
    converts None → empty string via the ``or ""`` fallback. Covers
    the else branch when exc.stdout is not bytes."""
    from channel.sandbox import handler as handler_module

    monkeypatch.setattr(handler_module, "_SUBPROCESS_TIMEOUT_SEC", 0.5)
    # Code that blocks immediately without printing — exc.stdout will
    # be None (nothing was captured before the timeout fired).
    result = handler_module.lambda_handler(
        {"code": "import time; time.sleep(5)"}, None
    )

    assert result["timed_out"] is True
    assert result["exit_code"] == -1
    assert result["stdout"] == ""
    assert result["stderr"] == ""


def test_handler_timeout_with_bytes_stderr_decodes(monkeypatch):
    """Verify the timeout-branch stderr bytes-decode path runs. Forces
    exc.stderr to be bytes so the ``decode("utf-8", errors="replace")``
    branch on line 109 executes. Covers that branch."""
    import subprocess
    from channel.sandbox import handler as handler_module

    real_run = subprocess.run

    def stub_run(*args, **kwargs):
        # Raise TimeoutExpired with bytes for stderr (forces the
        # bytes-decode branch to fire). exc.stdout is also bytes so
        # the matching stdout branch fires, but the assertion focuses
        # on stderr.
        raise subprocess.TimeoutExpired(
            cmd=args[0] if args else kwargs.get("args"),
            timeout=1.0,
            output=b"partial out",
            stderr=b"partial err",
        )
    monkeypatch.setattr(handler_module.subprocess, "run", stub_run)

    result = handler_module.lambda_handler({"code": "anything"}, None)

    assert result["timed_out"] is True
    assert result["stdout"] == "partial out"
    assert result["stderr"] == "partial err"


def test_handler_harvests_png_images_from_tmp(monkeypatch, tmp_path):
    """Pre-seed a PNG in the sandbox tmp dir → handler base64-encodes
    it post-exec and returns it in ``images``.

    The test injects the PNG bytes directly via ``write_bytes`` rather
    than via user code so we can hold ``_TMP_DIR`` constant across the
    wipe + subprocess. _wipe_tmp is patched to no-op for this test so
    the pre-seeded file survives handler entry."""
    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))
    monkeypatch.setattr(handler_module, "_wipe_tmp", lambda: None)

    # Minimal valid 1×1 transparent PNG (~70 bytes).
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d"
        "49444154789c63000000050001010d0a2db40000000049454e44ae426082"
    )
    (fake_tmp / "plot.png").write_bytes(png_bytes)

    result = handler_module.lambda_handler({"code": "pass"}, None)

    assert len(result["images"]) == 1
    img = result["images"][0]
    assert img["mime"] == "image/png"
    assert isinstance(img["b64"], str)
    assert len(img["b64"]) > 0


def test_handler_caps_image_count_at_three(monkeypatch, tmp_path):
    """5 PNGs in /tmp → only the 3 most recent (by mtime) are returned."""
    import time as time_mod

    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))
    monkeypatch.setattr(handler_module, "_wipe_tmp", lambda: None)

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 100   # not a valid PNG but the
    # handler doesn't parse — it relies on extension
    for i in range(5):
        path = fake_tmp / f"plot_{i}.png"
        path.write_bytes(png_bytes)
        # Set staggered mtimes so the harvest can order deterministically
        t = time_mod.time() + i
        os.utime(path, (t, t))

    result = handler_module.lambda_handler({"code": "pass"}, None)

    assert len(result["images"]) == 3


def test_handler_skips_images_over_size_cap(monkeypatch, tmp_path):
    """A 2 MB PNG is skipped (over the 1 MB per-image cap)."""
    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))
    monkeypatch.setattr(handler_module, "_wipe_tmp", lambda: None)

    big = b"\x89PNG\r\n\x1a\n" + (b"x" * (2 * 1024 * 1024))
    (fake_tmp / "big.png").write_bytes(big)
    small = b"\x89PNG\r\n\x1a\n" + (b"y" * 100)
    (fake_tmp / "small.png").write_bytes(small)

    result = handler_module.lambda_handler({"code": "pass"}, None)

    # Only the small one survives the per-image cap.
    assert len(result["images"]) == 1


def test_handler_handles_no_images_in_tmp():
    """Empty /tmp → ``images`` is an empty list, no crash."""
    from channel.sandbox.handler import lambda_handler

    result = lambda_handler({"code": "print('no images')"}, None)

    assert result["images"] == []


def test_harvest_tmp_images_skips_stat_oserror(monkeypatch, tmp_path):
    """If os.stat raises OSError on a candidate file, harvest skips it
    and continues with the next. Covers the except OSError: continue
    branch in the listdir loop."""
    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))

    # Write a valid PNG and an invalid one.
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    (fake_tmp / "good.png").write_bytes(png_bytes)
    (fake_tmp / "bad.png").write_bytes(png_bytes)

    # Force os.stat to raise OSError for "bad.png" only.
    real_stat = os.stat
    def selective_stat(path):
        if "bad.png" in str(path):
            raise OSError("simulated stat error")
        return real_stat(path)
    monkeypatch.setattr(handler_module.os, "stat", selective_stat)

    result = handler_module.lambda_handler({"code": "pass"}, None)

    # Only the good one should be harvested.
    assert len(result["images"]) == 1


def test_harvest_tmp_images_skips_open_oserror(monkeypatch, tmp_path):
    """If open(path, 'rb').read() raises OSError, harvest skips that
    image and continues. Covers the except OSError: continue branch
    in the data-read loop."""
    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    (fake_tmp / "readable.png").write_bytes(png_bytes)
    (fake_tmp / "unreadable.png").write_bytes(png_bytes)

    # Force open to raise OSError for "unreadable.png" only.
    real_open = open
    def selective_open(path, *args, **kwargs):
        if "unreadable.png" in str(path):
            raise OSError("simulated open error")
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr("builtins.open", selective_open)

    result = handler_module.lambda_handler({"code": "pass"}, None)

    # Only the readable one should be harvested.
    assert len(result["images"]) == 1
