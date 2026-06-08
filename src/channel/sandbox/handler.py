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
import contextlib
import os
import shutil
import signal
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


def _killpg_quiet(pgid: int) -> None:
    """Send SIGKILL to the process group identified by ``pgid``.

    Idempotent: ``ProcessLookupError`` (group already gone — the
    normal-exit path) and ``PermissionError`` (race where init has
    reparented some children) are swallowed. The whole point is to
    close the orphan-child containment hole; if the group is already
    cleaned up, that's the desired end state."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


def _cap(text: str, limit: int) -> tuple[str, bool]:
    """Truncate ``text`` to ``limit`` bytes (UTF-8 encoded length) and
    append a marker if the original was longer. Returns ``(capped, was_truncated)``.

    Single-pass O(n): encode once, slice once, decode with
    ``errors="ignore"`` so any partial UTF-8 sequence at the cut
    boundary is dropped cleanly (no replacement character left at the
    end). Earlier implementations re-encoded the prefix per character
    drop which made this O(n²) on long outputs (Copilot review of
    PR #229)."""
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return (text, False)
    overflow = len(encoded) - limit
    marker_bytes = f"…[truncated, {overflow} more bytes]".encode()
    available = limit - len(marker_bytes)
    if available <= 0:
        # Pathological tiny limit — return just the marker, truncated
        # to fit. Never observed in practice (limits are 5 KB and 20 KB)
        # but the bound prevents a negative-index slice below.
        return (marker_bytes[:limit].decode("utf-8", errors="replace"), True)
    return (
        encoded[:available].decode("utf-8", errors="ignore") + marker_bytes.decode("utf-8"),
        True,
    )


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
    # Popen + ``start_new_session=True`` puts the child Python in its
    # own process group (pgid == pid). User code that does
    # ``subprocess.Popen([...]); sys.exit(0)`` would orphan grandchild
    # processes that survive into the Lambda warm pool — consuming
    # CPU and racing the post-exec /tmp harvest (TOCTOU). Killing the
    # whole process group in ``finally`` collapses that surface.
    #
    # Explicit ``encoding`` + ``errors="replace"`` makes stdout/stderr
    # capture robust to non-UTF-8 bytes the subprocess might write
    # (e.g. ``sys.stdout.buffer.write(b'\xff\xfe')``) — without
    # this, Python's ``text=True`` decoder raises UnicodeDecodeError
    # and the handler would crash on otherwise-valid runs.
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=_TMP_DIR,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    pgid = proc.pid
    try:
        try:
            stdout, stderr = proc.communicate(timeout=_SUBPROCESS_TIMEOUT_SEC)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            # Kill the whole group BEFORE the second communicate() so
            # children blocked on a pipe don't keep stdout/stderr open
            # and stall the read.
            _killpg_quiet(pgid)
            stdout, stderr = proc.communicate()
            exit_code = -1
            timed_out = True
    finally:
        # Defense in depth: even on the happy path the Python -c parent
        # may have exited after spawning a long-running orphan. Killing
        # the group here is idempotent — ProcessLookupError on the
        # normal-exit path is expected and swallowed by ``_killpg_quiet``.
        _killpg_quiet(pgid)
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
