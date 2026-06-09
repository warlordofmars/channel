# Copyright (c) 2026 John Carter. All rights reserved.
"""Lambda handler for the code-execution sandbox (#183).

Runs user-supplied Python code under ``subprocess.Popen`` (with
``start_new_session=True`` so the child gets its own process group)
plus ``proc.wait(timeout=...)`` for the wall-clock cap. stdout and
stderr are drained in dedicated daemon reader threads with hard
per-stream byte budgets — bounded memory regardless of how much the
subprocess writes, and the reader kills the process group immediately
when its budget is exceeded so a runaway ``print`` loop doesn't tie
up the sandbox slot for the full timeout. The process group is also
killed via ``os.killpg`` on the timeout-recovery path and a
``finally`` defense-in-depth so user code that spawns grandchildren
(e.g. ``subprocess.Popen([...]); sys.exit(0)``) doesn't leak orphans
into the Lambda warm container.

Constraints:

* a 270 s wall-clock cap (30 s short of the 5-min Lambda timeout so
  the handler can serialize a ``{timed_out: true}`` response)
* a 20 KB stdout cap and 5 KB stderr cap with truncation markers
* ``/tmp`` wiped at entry to close warm-container leakage between
  invocations
* ``/tmp`` scanned post-exec for ``*.png`` / ``*.jpg`` / ``*.jpeg``,
  first 3 by mtime returned base64-encoded (up to 1 MB each)
* output capture uses ``encoding="utf-8", errors="replace"`` so user
  code that writes non-UTF-8 bytes gets replacement chars rather
  than crashing the handler with UnicodeDecodeError

The handler runs in its own Lambda function with an IAM role granting
nothing beyond ``AWSLambdaBasicExecutionRole`` (CloudWatch Logs only).
Sandbox cannot reach Channel data even if user code escapes the
subprocess. Note: outbound internet IS reachable — see the tool
docstring for the honest network-isolation posture; hard no-egress
VPC containment is a follow-up.
"""

from __future__ import annotations

import base64
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import threading
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


def _start_pipe_reader(
    pipe,
    chunks: list[str],
    budget: int,
    on_overflow=None,
) -> threading.Thread:
    """Spawn a daemon thread that reads ``pipe`` into ``chunks`` until
    either EOF or ``budget`` bytes have been buffered (whichever comes
    first), then exits. Returns the thread so the caller can join it.

    Bounded-memory guarantee: at most ``budget + chunk_size`` bytes
    sit in memory per stream regardless of how much the subprocess
    writes. Once the budget is hit, ``on_overflow()`` is invoked
    (typically to kill the subprocess process group) so the reader
    doesn't tie up the sandbox slot waiting for the 270s wall-clock
    timer — a runaway ``print('x' * 10**9)`` loop should fail fast,
    not look like a timeout. Without ``on_overflow`` the reader just
    stops reading and the subprocess will eventually block on its
    next pipe write."""

    def _pump() -> None:
        total = 0
        try:
            while total < budget:
                chunk = pipe.read(4096)
                if not chunk:
                    return
                chunks.append(chunk)
                total += len(chunk)
            if on_overflow is not None:
                on_overflow()
        except (OSError, ValueError):
            # Pipe was closed mid-read (subprocess killed). Normal
            # exit path; nothing else to do.
            return

    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    return t


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


def _collect_image_candidates() -> list[tuple[float, str, int]]:
    """Return a list of ``(mtime, path, st_size)`` tuples for files in
    ``_TMP_DIR`` whose extension matches ``_IMAGE_EXTENSIONS``. Sorted
    newest-first by mtime so the caller can take the most-recent
    ``_MAX_IMAGES`` without walking the whole list. Files we can't
    stat (permission errors, broken symlinks) are silently skipped."""
    if not os.path.isdir(_TMP_DIR):
        return []
    candidates: list[tuple[float, str, int]] = []
    for name in os.listdir(_TMP_DIR):
        if not name.lower().endswith(_IMAGE_EXTENSIONS):
            continue
        path = os.path.join(_TMP_DIR, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        candidates.append((stat.st_mtime, path, stat.st_size))
    candidates.sort(reverse=True)
    return candidates


def _read_image_safely(path: str, st_size: int, real_tmp: str) -> dict[str, str] | None:
    """Try to read one harvest candidate into a base64-encoded image dict.

    Returns ``None`` if the file fails any safety check:
    - st_size over the per-image cap (pre-read short-circuit so we
      never even open multi-MB files)
    - realpath fails (broken symlink etc.)
    - realpath resolves outside ``_TMP_DIR`` (symlink exfiltration
      defense — see the security note on ``_harvest_tmp_images``)
    - open/read fails (permission error)
    - post-read length exceeds the cap (TOCTOU backstop — file could
      have grown between stat and open)"""
    if st_size > _MAX_IMAGE_BYTES:
        return None
    try:
        real_path = os.path.realpath(path)
    except OSError:
        return None
    # Reject symlinks that resolve outside _TMP_DIR. os.sep guards
    # against the "/tmp/xyz" vs "/tmp-something" prefix-match trap.
    if not (real_path == real_tmp or real_path.startswith(real_tmp + os.sep)):
        return None
    try:
        with open(real_path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) > _MAX_IMAGE_BYTES:
        return None
    ext = os.path.splitext(path)[1].lower()
    return {
        "mime": _IMAGE_MIME.get(ext, "application/octet-stream"),
        "b64": base64.b64encode(data).decode("ascii"),
    }


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

    The post-exec scan happens AFTER ``proc.wait()`` returns (whether
    via normal exit, timeout-kill, or budget-overflow-kill) and BEFORE
    the response is serialized. Empty /tmp returns ``[]``."""
    candidates = _collect_image_candidates()
    if not candidates:
        return []
    real_tmp = os.path.realpath(_TMP_DIR)
    out: list[dict[str, str]] = []
    for _mtime, path, st_size in candidates:
        if len(out) >= _MAX_IMAGES:
            break
        image = _read_image_safely(path, st_size, real_tmp)
        if image is not None:
            out.append(image)
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
    # Bounded-memory streaming capture: read stdout/stderr in worker
    # threads with a hard per-stream byte budget so user code that
    # ``print('x' * 100_000_000)`` can't OOM the Lambda BEFORE the
    # post-run ``_cap`` truncates. Budgets are 2× the soft cap to
    # leave headroom for the truncation marker. When a reader hits
    # its budget it stops reading; the subprocess will block on the
    # next pipe write and either finish naturally (small) or hit the
    # 270s timeout (and we kill the group). Either way the handler's
    # memory footprint stays bounded.
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    # When a reader hits its budget we kill the process group
    # immediately. Otherwise the subprocess would block on its next
    # pipe write and we'd wait the full 270s wall-clock cap before
    # cleaning up — wasting reserved-concurrency slot capacity on
    # runaway output that we already know we're going to truncate.

    def _kill_on_overflow() -> None:
        _killpg_quiet(pgid)

    stdout_thread = _start_pipe_reader(
        proc.stdout, stdout_chunks, _STDOUT_CAP * 2, on_overflow=_kill_on_overflow
    )
    stderr_thread = _start_pipe_reader(
        proc.stderr, stderr_chunks, _STDERR_CAP * 2, on_overflow=_kill_on_overflow
    )
    try:
        try:
            proc.wait(timeout=_SUBPROCESS_TIMEOUT_SEC)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            # Kill the whole group so the reader threads' pipes EOF
            # and the threads can drain + exit cleanly.
            _killpg_quiet(pgid)
            proc.wait()
            exit_code = -1
            timed_out = True
    finally:
        # Defense in depth: even on the happy path the Python -c parent
        # may have exited after spawning a long-running orphan. Killing
        # the group here is idempotent — ProcessLookupError on the
        # normal-exit path is expected and swallowed by ``_killpg_quiet``.
        _killpg_quiet(pgid)
    # Reader threads exit when their pipes EOF (subprocess finished +
    # closed stdio) or when they hit the hard byte budget. Join with
    # a bounded timeout so chunk lists are fully populated before we
    # read them — if a reader is stuck, the subprocess has already
    # been killed and the pipe's underlying fd should close imminently.
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    duration_ms = int((time.monotonic() - start) * 1000)
    stdout, stdout_trunc = _cap("".join(stdout_chunks), _STDOUT_CAP)
    stderr, stderr_trunc = _cap("".join(stderr_chunks), _STDERR_CAP)
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
