# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the code-exec sandbox Lambda handler (#183).

The handler is a pure Lambda entrypoint — no boto3, no AWS calls. These
tests cover the subprocess driver, length caps, /tmp policy, and image
harvest. Tool-wrapper boto3 / SSE behavior lives in
``test_tools_code_exec.py``."""

from __future__ import annotations

import base64
import os
import time

import pytest


@pytest.fixture(autouse=True)
def _isolate_tmp_dir(monkeypatch, tmp_path):
    """Redirect every test's view of ``_TMP_DIR`` to a per-test scratch
    directory so ``_wipe_tmp`` can't blow away pytest's own scaffolding
    at ``/tmp/pytest-of-runner/...`` on Linux CI.

    macOS local dev hides this risk because pytest stores its tmp under
    ``/private/var/folders/.../pytest-current``, NOT under ``/tmp``
    (which is itself a symlink to ``/private/tmp``). On Linux CI
    pytest's tmp IS in ``/tmp``, so a test that runs
    ``lambda_handler`` without monkeypatching ``_TMP_DIR`` would
    silently wipe pytest's working directory on the very next test."""
    from channel.sandbox import handler as handler_module

    # NAME deliberately doesn't collide with the per-test "sandbox-tmp"
    # subdirs that test_handler_wipes_tmp_on_entry et al. create — those
    # tests setattr _TMP_DIR a SECOND time inside the test body to their
    # own scratch dir. monkeypatch's stack-then-restore semantics handle
    # the nested overrides cleanly.
    safe_default_tmp = tmp_path / "default-sandbox-tmp"
    safe_default_tmp.mkdir()
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(safe_default_tmp))
    yield safe_default_tmp


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


def _spy_popen(monkeypatch, handler_module):
    """Wrap ``subprocess.Popen`` with a spy that captures the ``env``
    + ``start_new_session`` kwargs while still running the subprocess
    for real (so the rest of the handler — communicate, harvest,
    cap — exercises against actual output)."""
    import subprocess as subprocess_mod

    captured: dict[str, object] = {}
    real_popen = subprocess_mod.Popen

    def spy_popen(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        captured["start_new_session"] = kwargs.get("start_new_session")
        captured["encoding"] = kwargs.get("encoding")
        captured["errors"] = kwargs.get("errors")
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(handler_module.subprocess, "Popen", spy_popen)
    return captured


def test_handler_subprocess_pythonpath_includes_lambda_task_root(monkeypatch):
    """Subprocess gets ``PYTHONPATH`` set to ``LAMBDA_TASK_ROOT`` so
    bundled packages (numpy / pandas / matplotlib) are importable in
    user code. Without this, the subprocess starts with a clean
    sys.path and ``import matplotlib`` fails despite matplotlib being
    bundled into /var/task at deploy time. Captured by the 2026-06-08
    dev smoke."""
    from channel.sandbox import handler as handler_module

    captured = _spy_popen(monkeypatch, handler_module)
    monkeypatch.setenv("LAMBDA_TASK_ROOT", "/sentinel-task-root")

    handler_module.lambda_handler({"code": "print('x')"}, None)

    env = captured["env"]
    assert isinstance(env, dict)
    pythonpath = env.get("PYTHONPATH", "")
    assert "/sentinel-task-root" in pythonpath


def test_handler_subprocess_pythonpath_defaults_to_var_task(monkeypatch):
    """When ``LAMBDA_TASK_ROOT`` is unset (local dev / unit test),
    fall back to the documented Lambda task-root path ``/var/task``."""
    from channel.sandbox import handler as handler_module

    captured = _spy_popen(monkeypatch, handler_module)
    monkeypatch.delenv("LAMBDA_TASK_ROOT", raising=False)

    handler_module.lambda_handler({"code": "print('x')"}, None)

    env = captured["env"]
    assert isinstance(env, dict)
    pythonpath = env.get("PYTHONPATH", "")
    assert "/var/task" in pythonpath


def test_handler_subprocess_pythonpath_preserves_existing(monkeypatch):
    """If ``PYTHONPATH`` is already set in the env (e.g. layer-mounted
    extras), the task-root is PREPENDED so user code preserves access
    to the original entries too."""
    from channel.sandbox import handler as handler_module

    captured = _spy_popen(monkeypatch, handler_module)
    monkeypatch.setenv("LAMBDA_TASK_ROOT", "/sentinel-task-root")
    monkeypatch.setenv("PYTHONPATH", "/opt/python")

    handler_module.lambda_handler({"code": "print('x')"}, None)

    env = captured["env"]
    assert isinstance(env, dict)
    pythonpath = env.get("PYTHONPATH", "")
    # Task root prepended; original entry retained.
    assert pythonpath == "/sentinel-task-root:/opt/python"


def test_handler_subprocess_uses_explicit_utf8_replace(monkeypatch):
    """Output capture is explicitly ``encoding="utf-8", errors="replace"``
    so user code that writes non-UTF-8 bytes
    (``sys.stdout.buffer.write(b'\\xff\\xfe')``) gets replacement chars
    rather than crashing the handler with UnicodeDecodeError."""
    from channel.sandbox import handler as handler_module

    captured = _spy_popen(monkeypatch, handler_module)

    handler_module.lambda_handler({"code": "print('x')"}, None)

    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


def test_handler_subprocess_starts_new_session(monkeypatch):
    """``start_new_session=True`` puts the child Python in its own
    process group so orphan grandchildren can be killed in
    ``finally`` via ``os.killpg``."""
    from channel.sandbox import handler as handler_module

    captured = _spy_popen(monkeypatch, handler_module)

    handler_module.lambda_handler({"code": "print('x')"}, None)

    assert captured["start_new_session"] is True


def test_handler_kills_process_group_on_normal_exit(monkeypatch):
    """Even on the happy path, ``_killpg_quiet`` fires in ``finally`` to
    collapse any orphan children. The Python ``-c`` parent has already
    exited so ``os.killpg`` raises ``ProcessLookupError``; the helper
    swallows it."""
    from channel.sandbox import handler as handler_module

    killpg_calls = []

    def spy_killpg(pgid, sig):
        killpg_calls.append((pgid, sig))
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(handler_module.os, "killpg", spy_killpg)

    result = handler_module.lambda_handler({"code": "print('x')"}, None)

    # Happy path completed; one killpg call from the finally block.
    assert result["exit_code"] == 0
    assert len(killpg_calls) == 1
    assert killpg_calls[0][1] == handler_module.signal.SIGKILL


def test_killpg_quiet_swallows_permission_error(monkeypatch):
    """``_killpg_quiet`` also catches ``PermissionError`` (race where
    init has reparented some children). Defensive — the test is direct
    on the helper since this branch is hard to provoke via the
    Popen path."""
    from channel.sandbox import handler as handler_module

    def boom(_pgid, _sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(handler_module.os, "killpg", boom)
    handler_module._killpg_quiet(12345)  # must not raise


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


def test_cap_walks_back_to_utf8_boundary_on_multibyte_input():
    """``_cap`` cuts at ``limit`` bytes then walks back to the nearest
    UTF-8 start byte so we don't return a mid-rune-truncated string.

    Each "你" is 3 bytes in UTF-8. We feed 10 of them (30 bytes) and
    request a limit that lands mid-rune to exercise the continuation-
    byte walk-back loop on line 70."""
    from channel.sandbox.handler import _cap

    # 100×3-byte runes = 300 encoded bytes. With limit=60 and a
    # ~29-byte marker ("…[truncated, 240 more bytes]") we have ~31
    # bytes for content — the byte-cut at 31 lands mid-rune
    # (continuation byte) and the walk-back fires.
    text = "你" * 100
    capped, truncated = _cap(text, 60)

    assert truncated is True
    # No "REPLACEMENT CHARACTER" (�) in the prefix — that's what
    # we'd see if the cut had split a rune mid-byte.
    prefix = capped.split("…")[0]
    assert "�" not in prefix
    # Whole runes only in the prefix (each rune is 3 bytes).
    assert len(prefix.encode("utf-8")) % 3 == 0


def test_cap_returns_marker_only_on_pathological_tiny_limit():
    """If ``limit`` is smaller than the marker (never observed in
    production — caps are 5 KB / 20 KB) ``_cap`` returns just the
    marker bytes truncated to fit. Covers the defensive ``available
    <= 0`` branch."""
    from channel.sandbox.handler import _cap

    # Force overflow with a tiny limit that the marker can't fit in.
    capped, truncated = _cap("x" * 1000, 5)

    assert truncated is True
    assert len(capped.encode("utf-8")) <= 5


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

    monkeypatch.setattr(handler_module, "_TMP_DIR", "/nonexistent-path-for-test-12345")

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


def test_wipe_tmp_removes_subdirectories_recursively(monkeypatch, tmp_path):
    """``_wipe_tmp`` recursively rmtree's subdirectories under
    ``_TMP_DIR``. Pre-fix this branch only fired on Linux CI when tests
    accidentally pointed at the real /tmp (which always has OS dirs in
    it). Now we exercise it deliberately."""
    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    nested = fake_tmp / "leftover-dir"
    nested.mkdir()
    (nested / "inside.txt").write_text("from a prior invocation")
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))

    handler_module._wipe_tmp()

    assert not nested.exists(), "subdirectory under _TMP_DIR must be recursively removed"


def test_handler_timeout_with_no_stdout_returns_empty_string(monkeypatch):
    """When the subprocess times out without producing any stdout (the
    code blocks before any output), the post-kill drain ``communicate()``
    returns empty strings (with text-mode encoding + errors=replace,
    never None or bytes)."""
    from channel.sandbox import handler as handler_module

    monkeypatch.setattr(handler_module, "_SUBPROCESS_TIMEOUT_SEC", 0.5)
    # Code that blocks immediately without printing — exc.stdout will
    # be None (nothing was captured before the timeout fired).
    result = handler_module.lambda_handler({"code": "import time; time.sleep(5)"}, None)

    assert result["timed_out"] is True
    assert result["exit_code"] == -1
    assert result["stdout"] == ""
    assert result["stderr"] == ""


def test_start_pipe_reader_stops_at_byte_budget():
    """Streaming cap: the reader stops accumulating after the budget
    is met, so user code that prints hundreds of MB can't OOM the
    Lambda before the post-run ``_cap`` truncates."""
    from channel.sandbox import handler as handler_module

    # A pipe that produces 1 MB per read forever.
    class _FloodingPipe:
        def read(self, size):
            return "x" * size

    chunks: list[str] = []
    # Budget 10 KB; chunk size in _start_pipe_reader is 4 KB.
    t = handler_module._start_pipe_reader(_FloodingPipe(), chunks, budget=10_000)
    t.join(timeout=2)
    assert not t.is_alive()
    # Total buffered = at most budget + chunk_size (one final chunk
    # could land before the loop notices it crossed the budget).
    total = sum(len(c) for c in chunks)
    assert total >= 10_000
    assert total <= 10_000 + 4096


def test_start_pipe_reader_invokes_on_overflow_when_budget_exceeded():
    """When a flood overshoots the budget, the reader fires
    ``on_overflow``. lambda_handler wires this to kill the subprocess
    group so a runaway ``print('x' * 10**9)`` loop fails fast instead
    of tying up the sandbox slot for the full 270s wall-clock cap."""
    from channel.sandbox import handler as handler_module

    class _FloodingPipe:
        def read(self, size):
            return "x" * size

    overflow_calls: list[bool] = []
    chunks: list[str] = []
    t = handler_module._start_pipe_reader(
        _FloodingPipe(),
        chunks,
        budget=1000,
        on_overflow=lambda: overflow_calls.append(True),
    )
    t.join(timeout=2)
    assert not t.is_alive()
    assert overflow_calls == [True]


def test_handler_kills_subprocess_on_stdout_overflow(monkeypatch):
    """End-to-end: when user code's stdout exceeds the streaming
    budget, the reader's ``on_overflow`` callback fires and kills the
    process group. The handler returns FAST (without waiting for the
    270s wall-clock timer), so a runaway ``print`` can't tie up the
    sandbox slot. Covers the ``_kill_on_overflow`` closure body."""
    from channel.sandbox import handler as handler_module

    # Shrink the soft cap so the budget (2x = 200 bytes) is small
    # enough that a single subprocess print() round-trips through
    # overflow quickly.
    monkeypatch.setattr(handler_module, "_STDOUT_CAP", 100)

    start = time.monotonic()
    # Print 5x250 bytes = 1250 bytes total (>> 200-byte budget).
    result = handler_module.lambda_handler(
        {
            "code": "import sys\nfor _ in range(5):\n    sys.stdout.write('x' * 250)\n    sys.stdout.flush()\n",
        },
        None,
    )
    duration_seconds = time.monotonic() - start

    # The subprocess was killed by the overflow path. exit_code is
    # nonzero (Python's exit on SIGTERM/SIGKILL). truncated=True
    # because stdout exceeded _STDOUT_CAP.
    assert result["truncated"] is True
    # Critically: the handler returned FAR before the 270s timer.
    assert duration_seconds < 30, (
        f"Overflow kill path should complete promptly; took {duration_seconds:.2f}s"
    )
    # timed_out stays False — this is the overflow path, not the timer.
    assert result["timed_out"] is False


def test_start_pipe_reader_does_not_call_on_overflow_on_eof():
    """If the pipe EOFs before the budget is reached (the normal
    happy path — small output), ``on_overflow`` MUST NOT fire."""
    from channel.sandbox import handler as handler_module

    class _SmallPipe:
        def __init__(self):
            self._sent = False

        def read(self, _size):
            if self._sent:
                return ""
            self._sent = True
            return "hello"

    overflow_calls: list[bool] = []
    chunks: list[str] = []
    t = handler_module._start_pipe_reader(
        _SmallPipe(),
        chunks,
        budget=100_000,
        on_overflow=lambda: overflow_calls.append(True),
    )
    t.join(timeout=2)
    assert not t.is_alive()
    assert overflow_calls == []
    assert chunks == ["hello"]


def test_start_pipe_reader_swallows_oserror_when_pipe_closes_mid_read(monkeypatch):
    """The reader thread's ``except (OSError, ValueError)`` swallows
    the race where the subprocess gets killed mid-read and the pipe
    fd closes underneath the read() call. Defensive — exercises the
    catch directly since orchestrating the race via real subprocess
    is fragile across platforms."""
    import threading

    from channel.sandbox import handler as handler_module

    class _DyingPipe:
        def read(self, _size):
            raise OSError("simulated mid-read closure")

    chunks: list[str] = []
    t = handler_module._start_pipe_reader(_DyingPipe(), chunks, budget=1000)
    # Thread should swallow the exception and exit cleanly.
    t.join(timeout=2)
    assert not t.is_alive()
    assert chunks == []
    # Sanity: a clean thread API is what the lambda_handler expects.
    assert isinstance(t, threading.Thread)


def test_handler_timeout_kills_process_group_and_drains_readers(monkeypatch):
    """On timeout the handler kills the whole process group, the reader
    threads see EOF on their pipes and exit, and the final wait() in
    the except block reaps the zombie. Verifies the ``_killpg_quiet``
    call lands between the wait-timeout and the second wait(), and the
    finally-block fires once more idempotently."""
    import subprocess as subprocess_mod

    from channel.sandbox import handler as handler_module

    killpg_calls: list[int] = []
    wait_calls: list[float | None] = []

    class _ClosedPipe:
        """Stub pipe that immediately returns EOF so the reader thread
        exits cleanly without touching real fds."""

        def read(self, _size):
            return ""

    class FakeProc:
        def __init__(self):
            self.pid = 12345
            self.returncode = -9
            self.stdout = _ClosedPipe()
            self.stderr = _ClosedPipe()
            self._call = 0

        def wait(self, timeout=None):
            self._call += 1
            wait_calls.append(timeout)
            if self._call == 1:
                # First wait: timeout fires.
                raise subprocess_mod.TimeoutExpired(cmd="x", timeout=timeout)
            # Second wait: process has been killed; returns normally.
            return self.returncode

    def spy_killpg(pgid, _sig):
        killpg_calls.append(pgid)

    monkeypatch.setattr(handler_module.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(handler_module.os, "killpg", spy_killpg)

    result = handler_module.lambda_handler({"code": "anything"}, None)

    assert result["timed_out"] is True
    assert result["exit_code"] == -1
    # No output from the stubbed closed pipes.
    assert result["stdout"] == ""
    assert result["stderr"] == ""
    # killpg fires twice: once in the timeout-recovery path, once in
    # the finally defense-in-depth.
    assert killpg_calls == [12345, 12345]
    # wait() called twice: first with the configured timeout, then
    # post-kill with no timeout to reap.
    assert len(wait_calls) == 2
    assert wait_calls == [handler_module._SUBPROCESS_TIMEOUT_SEC, None]


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

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 100  # not a valid PNG but the
    # handler doesn't parse — it relies on extension
    for i in range(5):
        path = fake_tmp / f"plot_{i}.png"
        path.write_bytes(png_bytes)
        # Set staggered mtimes so the harvest can order deterministically
        t = time_mod.time() + i
        os.utime(path, (t, t))

    result = handler_module.lambda_handler({"code": "pass"}, None)

    assert len(result["images"]) == 3


def test_harvest_tmp_images_skips_non_image_files(monkeypatch, tmp_path):
    """``_harvest_tmp_images`` walks ``_TMP_DIR`` and silently skips any
    file whose extension isn't in ``_IMAGE_EXTENSIONS``. Pre-fix this
    branch only fired on Linux CI when tests accidentally pointed at
    the real /tmp (which always has non-image files in it)."""
    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    (fake_tmp / "notes.txt").write_text("not an image")
    (fake_tmp / "data.json").write_text("{}")
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))
    monkeypatch.setattr(handler_module, "_wipe_tmp", lambda: None)

    result = handler_module.lambda_handler({"code": "pass"}, None)

    # Both non-image files skipped via the extension check.
    assert result["images"] == []


def test_handler_skips_images_over_size_cap(monkeypatch, tmp_path):
    """A 2 MB PNG is skipped at the pre-read st_size check — bytes
    never enter memory."""
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


def test_harvest_post_read_cap_catches_toctou_race(monkeypatch, tmp_path):
    """TOCTOU backstop: if ``os.stat`` reports a sub-cap size but
    ``open(...).read()`` returns more bytes (e.g. the file got
    replaced between stat and open), the post-read ``len(data)`` check
    rejects the image. The pre-read st_size check is the common-case
    optimization; this branch is the safety net."""
    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))
    monkeypatch.setattr(handler_module, "_wipe_tmp", lambda: None)

    # File on disk is BIG, but spy stat reports a small size — i.e.
    # the file grew between stat and read. The pre-cap passes; the
    # post-read len() check must reject it.
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024)
    (fake_tmp / "racy.png").write_bytes(payload)

    real_stat = os.stat

    def lying_stat(p, *args, **kwargs):
        s = real_stat(p, *args, **kwargs)
        if "racy.png" in str(p):

            class _S:
                st_mtime = s.st_mtime
                st_size = 100  # lie: claim it's tiny

            return _S()
        return s

    monkeypatch.setattr(handler_module.os, "stat", lying_stat)

    result = handler_module.lambda_handler({"code": "pass"}, None)

    # Pre-cap accepted (size claimed 100 bytes); post-read len() check
    # rejected (actual 2+ MB).
    assert result["images"] == []


def test_handler_handles_no_images_in_tmp():
    """Empty /tmp → ``images`` is an empty list, no crash."""
    from channel.sandbox.handler import lambda_handler

    result = lambda_handler({"code": "print('no images')"}, None)

    assert result["images"] == []


def test_harvest_tmp_images_skips_stat_oserror(monkeypatch, tmp_path):
    """If os.stat raises OSError on a candidate file, harvest skips it
    and continues with the next. Covers the except OSError: continue
    branch in the listdir loop.

    Patches os.stat at the module-level os reference, but with a stub
    that accepts arbitrary kwargs so pytest's own os.stat calls (which
    pass follow_symlinks=) still work."""
    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))
    monkeypatch.setattr(handler_module, "_wipe_tmp", lambda: None)

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    (fake_tmp / "good.png").write_bytes(png_bytes)
    (fake_tmp / "bad.png").write_bytes(png_bytes)

    real_stat = os.stat

    def selective_stat(path, *args, **kwargs):
        if "bad.png" in str(path):
            raise OSError("simulated stat error")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(handler_module.os, "stat", selective_stat)

    result = handler_module.lambda_handler({"code": "pass"}, None)

    # bad.png is skipped via the except OSError branch; good.png is harvested.
    assert len(result["images"]) == 1


def test_harvest_tmp_images_skips_open_oserror(monkeypatch, tmp_path):
    """If open(path, 'rb').read() raises OSError, harvest skips that
    image and continues. Covers the except OSError: continue branch
    in the data-read loop.

    Patches builtins.open with a stub that accepts arbitrary kwargs so
    pytest's own open() calls (which use buffering=, encoding=, etc.)
    still work."""
    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))
    monkeypatch.setattr(handler_module, "_wipe_tmp", lambda: None)

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    (fake_tmp / "readable.png").write_bytes(png_bytes)
    (fake_tmp / "unreadable.png").write_bytes(png_bytes)

    real_open = open

    def selective_open(path, *args, **kwargs):
        if "unreadable.png" in str(path):
            raise OSError("simulated open error")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", selective_open)

    result = handler_module.lambda_handler({"code": "pass"}, None)

    # unreadable.png is skipped via the except OSError branch; readable.png is harvested.
    assert len(result["images"]) == 1


def test_harvest_tmp_images_early_returns_when_dir_missing(monkeypatch):
    """If ``_TMP_DIR`` doesn't exist as a directory, ``_harvest_tmp_images``
    returns ``[]`` without raising. Covers the defensive early-return."""
    from channel.sandbox import handler as handler_module

    monkeypatch.setattr(handler_module, "_TMP_DIR", "/nonexistent-path-for-test-67890")

    assert handler_module._harvest_tmp_images() == []


def test_harvest_tmp_images_skips_realpath_oserror(monkeypatch, tmp_path):
    """If ``os.path.realpath`` raises OSError on a candidate file
    (e.g. broken symlink that triggers an ELOOP), harvest skips it
    and continues. Covers the ``except OSError: continue`` branch
    on the realpath resolution."""
    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))
    monkeypatch.setattr(handler_module, "_wipe_tmp", lambda: None)

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    (fake_tmp / "good.png").write_bytes(png_bytes)
    (fake_tmp / "broken.png").write_bytes(png_bytes)

    real_realpath = os.path.realpath

    def selective_realpath(path, *args, **kwargs):
        if "broken.png" in str(path):
            raise OSError("simulated ELOOP")
        return real_realpath(path, *args, **kwargs)

    monkeypatch.setattr(handler_module.os.path, "realpath", selective_realpath)

    result = handler_module.lambda_handler({"code": "pass"}, None)

    # Only the unbroken file survives the realpath check.
    assert len(result["images"]) == 1


def test_harvest_tmp_images_rejects_symlink_escape(monkeypatch, tmp_path):
    """User code can ``os.symlink('/etc/passwd', '/tmp/leak.png')`` to
    exfiltrate files outside ``_TMP_DIR``. Symlinks whose real-path
    resolves outside the tmp dir are skipped — they do NOT make it
    into the response."""
    from channel.sandbox import handler as handler_module

    fake_tmp = tmp_path / "sandbox-tmp"
    fake_tmp.mkdir()
    outside = tmp_path / "outside-secret.dat"
    outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"sensitive payload" * 4)

    monkeypatch.setattr(handler_module, "_TMP_DIR", str(fake_tmp))
    monkeypatch.setattr(handler_module, "_wipe_tmp", lambda: None)

    # Symlink with an image extension pointing OUTSIDE _TMP_DIR.
    os.symlink(str(outside), str(fake_tmp / "leak.png"))
    # A real harvestable image so we know the harvest path runs at all.
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    (fake_tmp / "ok.png").write_bytes(png_bytes)

    result = handler_module.lambda_handler({"code": "pass"}, None)

    # Only the legitimate ok.png survives; the symlink is rejected.
    assert len(result["images"]) == 1
    # And the encoded payload is the legitimate one, not the secret.
    img_bytes = base64.b64decode(result["images"][0]["b64"])
    assert b"sensitive payload" not in img_bytes


def test_handler_rejects_empty_code():
    """Empty string ``code`` → Strands-ToolResult-shaped error."""
    from channel.sandbox.handler import lambda_handler

    result = lambda_handler({"code": ""}, None)

    assert result == {
        "status": "error",
        "content": [{"text": "empty_code"}],
    }


def test_handler_rejects_missing_code_key():
    """No ``code`` in event → same error shape as empty string."""
    from channel.sandbox.handler import lambda_handler

    result = lambda_handler({}, None)

    assert result == {
        "status": "error",
        "content": [{"text": "empty_code"}],
    }


def test_handler_rejects_non_string_code():
    """``code`` is a list or dict → same error shape."""
    from channel.sandbox.handler import lambda_handler

    result = lambda_handler({"code": ["print('x')"]}, None)

    assert result == {
        "status": "error",
        "content": [{"text": "empty_code"}],
    }
