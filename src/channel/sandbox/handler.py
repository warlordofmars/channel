# Copyright (c) 2026 John Carter. All rights reserved.
"""Lambda handler for the code-execution sandbox (#183).

Runs user-supplied Python code under ``subprocess.run`` with:

* a 270 s wall-clock cap (30 s short of the 5-min Lambda timeout so
  the handler can serialize a ``{timed_out: true}`` response)
* a 20 KB stdout cap and 5 KB stderr cap with truncation markers
* ``/tmp`` wiped at entry to close warm-container leakage between
  invocations
* ``/tmp`` scanned post-exec for ``*.png`` / ``*.jpg`` / ``*.jpeg`` /
  ``*.svg``, first 3 by mtime returned base64-encoded (up to 1 MB
  each)

The handler runs in its own Lambda function with an IAM role granting
nothing beyond ``AWSLambdaBasicExecutionRole`` (CloudWatch Logs only).
Sandbox cannot reach Channel data even if user code escapes the
subprocess.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Any

_STDOUT_CAP = 20 * 1024
_STDERR_CAP = 5 * 1024
_SUBPROCESS_TIMEOUT_SEC = 270
_TMP_DIR = "/tmp"  # noqa: S108 — Lambda's writable scratch dir; injected for tests


def _cap(text: str, limit: int) -> tuple[str, bool]:
    """Truncate ``text`` to ``limit`` bytes (UTF-8 encoded length) and
    append a marker if the original was longer. Returns ``(capped, was_truncated)``.

    The marker takes up real bytes from the budget — we reserve 40
    bytes for it and trim ``text`` to ``limit - marker_len``. Trimming
    at a character boundary is critical: slicing bytes mid-rune would
    produce an invalid UTF-8 sequence."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return (text, False)
    overflow = len(encoded) - limit
    marker = f"…[truncated, {overflow} more bytes]"
    marker_len = len(marker.encode("utf-8"))
    # Trim by characters (not bytes) so we don't split a UTF-8 sequence.
    # Walk from the end of the str until the encoded prefix fits.
    head = text
    while len(head.encode("utf-8")) + marker_len > limit:
        head = head[:-1]
    return (head + marker, True)


def _wipe_tmp() -> None:
    """Remove all files and directories under ``_TMP_DIR``.

    Closes warm-container leakage: a previous invocation in the same
    Lambda container may have written sensitive intermediate files to
    /tmp. We clear them at the START of each invocation, not the end —
    wiping at the end doesn't protect THIS invocation from prior state.

    Walks shallowly (no recursion into deleted dirs) using
    ``shutil.rmtree`` per child. Errors are swallowed: a permission
    issue on a single file shouldn't fail the whole invocation."""
    if not os.path.isdir(_TMP_DIR):
        return
    for name in os.listdir(_TMP_DIR):
        path = os.path.join(_TMP_DIR, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.unlink(path)
        except OSError:
            pass


def lambda_handler(event: dict, _ctx: object) -> dict[str, Any]:
    _wipe_tmp()
    code = event.get("code", "")
    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SEC,
            cwd=_TMP_DIR,
            check=False,
        )
        stdout = proc.stdout
        stderr = proc.stderr
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        # Even with text=True, TimeoutExpired captures partial output as bytes.
        # Decode to str, or use "" if the subprocess produced no output.
        stdout = exc.stdout
        stderr = exc.stderr
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        else:
            stdout = stdout or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        else:
            stderr = stderr or ""
        exit_code = -1
        timed_out = True
    duration_ms = int((time.monotonic() - start) * 1000)
    stdout, stdout_trunc = _cap(stdout, _STDOUT_CAP)
    stderr, stderr_trunc = _cap(stderr, _STDERR_CAP)
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "truncated": stdout_trunc or stderr_trunc,
        "timed_out": timed_out,
        "images": [],
    }
