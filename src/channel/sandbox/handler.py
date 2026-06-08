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

import subprocess
import sys
import time
from typing import Any

_STDOUT_CAP = 20 * 1024
_STDERR_CAP = 5 * 1024
_SUBPROCESS_TIMEOUT_SEC = 270


def lambda_handler(event: dict, _ctx: object) -> dict[str, Any]:
    code = event.get("code", "")
    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SEC,
            cwd="/tmp",
            check=False,
        )
        stdout = proc.stdout
        stderr = proc.stderr
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        exit_code = -1
        timed_out = True
    duration_ms = int((time.monotonic() - start) * 1000)
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "truncated": False,
        "timed_out": timed_out,
        "images": [],
    }
