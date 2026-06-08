# Copyright (c) 2026 John Carter. All rights reserved.
"""Lambda handler for the code-execution sandbox (#183).

Runs user-supplied Python code under ``subprocess.run`` with:

* a 270 s wall-clock cap (30 s short of the 5-min Lambda timeout so
  the handler can serialize a ``{timed_out: true}`` response)
* a 20 KB stdout cap and 5 KB stderr cap with truncation markers
* ``/tmp`` wiped at entry to close warm-container leakage between
  invocations
* ``/tmp`` scanned post-exec for ``*.png`` / ``*.jpg`` / ``*.jpeg``,
  first 3 by mtime returned base64-encoded (up to 1 MB each)

The handler runs in its own Lambda function with an IAM role granting
nothing beyond ``AWSLambdaBasicExecutionRole`` (CloudWatch Logs only).
Sandbox cannot reach Channel data even if user code escapes the
subprocess.
"""

from __future__ import annotations

import base64
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
_MAX_IMAGES = 3
_MAX_IMAGE_BYTES = 1 * 1024 * 1024
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


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


def _harvest_tmp_images() -> list[dict[str, str]]:
    """Scan ``_TMP_DIR`` for image files and return up to ``_MAX_IMAGES``
    base64-encoded.

    Ordering: most-recent by mtime first. Per-image size cap
    (``_MAX_IMAGE_BYTES``) skips anything over 1 MB. Raster formats
    only (.png / .jpg / .jpeg) — SVG is excluded because inline SVG
    can carry executable script tags and the sandbox-to-browser path
    is too short to safely sanitize.

    Symlink resolution: user code can ``os.symlink('/etc/passwd',
    '/tmp/leak.png')`` to exfiltrate files outside ``_TMP_DIR``. Each
    candidate is resolved via ``os.path.realpath`` and the result must
    still sit under ``_TMP_DIR`` — otherwise the entry is skipped.
    Resolution happens AFTER the extension check so we don't waste
    syscalls on non-image files.

    The post-exec scan happens AFTER ``subprocess.run`` returns and
    BEFORE the response is serialized. Empty /tmp returns ``[]``."""
    if not os.path.isdir(_TMP_DIR):
        return []
    real_tmp = os.path.realpath(_TMP_DIR)
    candidates: list[tuple[float, str]] = []
    for name in os.listdir(_TMP_DIR):
        if not name.lower().endswith(_IMAGE_EXTENSIONS):
            continue
        path = os.path.join(_TMP_DIR, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        candidates.append((stat.st_mtime, path))
    candidates.sort(reverse=True)  # newest first
    out: list[dict[str, str]] = []
    for _mtime, path in candidates:
        if len(out) >= _MAX_IMAGES:
            break
        try:
            real_path = os.path.realpath(path)
        except OSError:
            continue
        # Reject symlinks that resolve outside _TMP_DIR — defense
        # against ``os.symlink('/etc/passwd', '/tmp/leak.png')`` style
        # exfiltration. ``os.sep`` guards against the "/tmp/xyz" vs
        # "/tmp-something" prefix-match trap.
        if not (real_path == real_tmp or real_path.startswith(real_tmp + os.sep)):
            continue
        try:
            data = open(real_path, "rb").read()  # noqa: SIM115 — short-lived
        except OSError:
            continue
        if len(data) > _MAX_IMAGE_BYTES:
            continue
        ext = os.path.splitext(path)[1].lower()
        out.append(
            {
                "mime": _IMAGE_MIME.get(ext, "application/octet-stream"),
                "b64": base64.b64encode(data).decode("ascii"),
            }
        )
    return out


def lambda_handler(event: dict, _ctx: object) -> dict[str, Any]:
    _wipe_tmp()
    code = event.get("code", "")
    if not isinstance(code, str) or not code:
        return {"status": "error", "content": [{"text": "empty_code"}]}
    start = time.monotonic()
    timed_out = False
    # Lambda doesn't set PYTHONPATH — it adds /var/task (the function
    # code root) to the parent's sys.path at runtime startup. Child
    # Python processes spawned via subprocess.run don't inherit that
    # mutation; they start with a clean sys.path. Without explicitly
    # propagating LAMBDA_TASK_ROOT, user code can't ``import numpy /
    # pandas / matplotlib`` despite those packages being bundled into
    # /var/task at deploy time. The 2026-06-08 dev smoke surfaced
    # this: the model reported "matplotlib isn't actually available".
    env = os.environ.copy()
    task_root = os.environ.get("LAMBDA_TASK_ROOT", "/var/task")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{task_root}:{existing}" if existing else task_root
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SEC,
            cwd=_TMP_DIR,
            check=False,
            env=env,
        )
        stdout = proc.stdout
        stderr = proc.stderr
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        # Even with text=True, TimeoutExpired captures partial output as
        # bytes-or-None on the exception (subprocess returns the raw
        # buffer it had captured at the moment the timer fired, before
        # it gets a chance to decode through the text-mode pipeline).
        # Normalize to str via decode/fallback.
        raw_stdout: bytes | str | None = exc.stdout
        raw_stderr: bytes | str | None = exc.stderr
        if isinstance(raw_stdout, bytes):
            stdout = raw_stdout.decode("utf-8", errors="replace")
        else:
            stdout = raw_stdout or ""
        if isinstance(raw_stderr, bytes):
            stderr = raw_stderr.decode("utf-8", errors="replace")
        else:
            stderr = raw_stderr or ""
        exit_code = -1
        timed_out = True
    duration_ms = int((time.monotonic() - start) * 1000)
    stdout, stdout_trunc = _cap(stdout, _STDOUT_CAP)
    stderr, stderr_trunc = _cap(stderr, _STDERR_CAP)
    images = _harvest_tmp_images()
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "truncated": stdout_trunc or stderr_trunc,
        "timed_out": timed_out,
        "images": images,
    }
