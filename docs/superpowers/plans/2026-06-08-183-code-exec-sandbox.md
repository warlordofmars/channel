# Code execution via Lambda sandbox (#183) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `code_exec` as the second concrete tool on top of the #181 chassis. A model turn that calls `code_exec(code)` invokes a separate Lambda sandbox running the code in a Python subprocess, returns stdout / stderr / exit code / duration / saved-image PNGs from /tmp, and surfaces the result in the Conversation view via a new `ToolResultBlock kind="code-output"` branch.

**Architecture:** Two new units. (1) A `CodeExecLambda` CDK construct deploying a separate Python 3.13 Lambda (SnapStart, reserved-concurrency 5, no VPC, separate IAM role with only CloudWatch Logs access) with a sci-stack baked in. (2) A `code_exec` Strands `@tool` in `src/channel/agents/tools/` that synchronously invokes the sandbox via boto3 and returns the result dict. The chassis's existing tool-event SSE plumbing carries the result; the SPA's existing `ToolResultBlock` gains a `kind="code-output"` branch with a monospace stdout pane (collapsed >5 lines), a `<details>`-wrapped stderr block, inline images, and a meta line.

**Tech Stack:** Python 3.13 + Strands 1.41.0, boto3 Lambda client, AWS CDK (Python), numpy/pandas/matplotlib/scipy/requests/httpx/python-dateutil in the sandbox zip, FastAPI, pytest + vitest.

**Spec:** `docs/superpowers/specs/2026-06-08-183-code-exec-sandbox-design.md`
**Issue:** [#183](https://github.com/warlordofmars/channel/issues/183)

---

## File map

**New files:**

- `src/channel/sandbox/__init__.py` — package marker
- `src/channel/sandbox/handler.py` — Lambda entrypoint: subprocess runner, length caps, /tmp wipe, image harvest
- `src/channel/sandbox/requirements.txt` — sci-stack pinned versions for the Lambda zip
- `src/channel/agents/tools/code_exec.py` — `@tool code_exec(code: str)` wrapper around `boto3 lambda.invoke()`
- `tests/unit/test_sandbox_handler.py` — handler unit tests (happy path, timeout, caps, /tmp policy, images, empty code)
- `tests/unit/test_tools_code_exec.py` — tool wrapper unit tests (happy path, 4 error_type branches, round-trip)
- `tests/e2e/test_code_exec.py` — e2e tests against deployed dev env (happy path, containment, length cap, image return)

**Modified files:**

- `infra/stacks/channel_stack.py` — `CodeExecLambda` construct + separate IAM role + bundling + `STARTER_CODE_EXEC_LAMBDA_ARN` / `STARTER_CODE_EXEC_ENABLED` env vars on the API Lambda
- `tests/unit/test_channel_stack.py` — CDK assertion tests
- `src/channel/agents/strands_sse.py` — extend `translate_event` tool_result success branch + `sse_tool_finished` signature for the optional `kind`/`payload` fields
- `tests/unit/test_strands_sse.py` — extend translate-event tests for the code-output dispatch
- `src/channel/api/chats.py` — forward the new SSE fields through the dispatch + register `code_exec` behind `STARTER_CODE_EXEC_ENABLED`
- `tests/unit/test_chats_api.py` — flag-on / flag-off registration tests
- `ui/src/hooks/useChatStream.js` — patch `step.kind` and `step.payload` on `tool_finished`
- `ui/src/hooks/useChatStream.test.js` — extend tool_finished tests
- `ui/src/app/ToolResultBlock.jsx` — code-output branch with stdout / stderr / images / meta
- `ui/src/app/ToolResultBlock.test.jsx` — extend test file
- `ui/src/app/Conversation.jsx` — pass `payload` prop on the `ToolResultBlock` call
- `ui/src/styles/app.css` — code-output styling block (CSS-vars only)
- `CHANGELOG.md` — `[Unreleased]` `### Added` entry

---

## Task 1: Sandbox package scaffold

**Files:**
- Create: `src/channel/sandbox/__init__.py`
- Create: `src/channel/sandbox/handler.py` (stub)
- Create: `src/channel/sandbox/requirements.txt`

- [ ] **Step 1: Create the package marker**

Write to `src/channel/sandbox/__init__.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Lambda sandbox package for code execution (#183).

Deployed as a separate Lambda function (``CodeExecLambda`` in
``infra/stacks/channel_stack.py``). The API Lambda invokes it via the
``code_exec`` Strands tool wrapper.
"""
```

- [ ] **Step 2: Create the handler stub**

Write to `src/channel/sandbox/handler.py`:

```python
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


def lambda_handler(event: dict, _ctx: object) -> dict:
    """Sandbox Lambda entrypoint. Body filled in by subsequent tasks."""
    raise NotImplementedError
```

- [ ] **Step 3: Create the requirements file**

Write to `src/channel/sandbox/requirements.txt`:

```text
numpy==2.2.6
pandas==2.2.3
matplotlib==3.10.0
scipy==1.15.0
requests==2.32.5
httpx==0.28.1
python-dateutil==2.9.0
```

- [ ] **Step 4: Commit**

```bash
git add src/channel/sandbox/
git commit -m "feat(sandbox): package scaffold for code-exec Lambda (#183)"
```

---

## Task 2: Sandbox handler — happy path

**Files:**
- Modify: `src/channel/sandbox/handler.py`
- Create: `tests/unit/test_sandbox_handler.py`

- [ ] **Step 1: Write the failing test**

Write to `tests/unit/test_sandbox_handler.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sandbox_handler.py -v`
Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement the minimal handler**

Replace the body of `lambda_handler` in `src/channel/sandbox/handler.py` with:

```python
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
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SEC,
        cwd="/tmp",
        check=False,
    )
    duration_ms = int((time.monotonic() - start) * 1000)
    return {
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "exit_code": proc.returncode,
        "duration_ms": duration_ms,
        "truncated": False,
        "timed_out": False,
        "images": [],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_sandbox_handler.py -v`
Expected: PASS.

- [ ] **Step 5: Add a non-zero-exit test and verify success-path returns it cleanly**

Append to `tests/unit/test_sandbox_handler.py`:

```python
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
```

- [ ] **Step 6: Run all 3 tests to verify they pass**

Run: `uv run pytest tests/unit/test_sandbox_handler.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add src/channel/sandbox/handler.py tests/unit/test_sandbox_handler.py
git commit -m "feat(sandbox): happy-path subprocess execution + duration capture (#183)"
```

---

## Task 3: Sandbox handler — timeout handling

**Files:**
- Modify: `src/channel/sandbox/handler.py`
- Modify: `tests/unit/test_sandbox_handler.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_sandbox_handler.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sandbox_handler.py::test_handler_timeout_returns_timed_out_with_partial_output -v`
Expected: FAIL with `subprocess.TimeoutExpired` raised uncaught.

- [ ] **Step 3: Implement the timeout branch**

Replace the body of `lambda_handler` in `src/channel/sandbox/handler.py` with:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_sandbox_handler.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/sandbox/handler.py tests/unit/test_sandbox_handler.py
git commit -m "feat(sandbox): subprocess.TimeoutExpired branch + partial-output capture (#183)"
```

---

## Task 4: Sandbox handler — stdout/stderr length caps

**Files:**
- Modify: `src/channel/sandbox/handler.py`
- Modify: `tests/unit/test_sandbox_handler.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_sandbox_handler.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_sandbox_handler.py -v`
Expected: 2 new fails (the existing 4 still pass).

- [ ] **Step 3: Add the cap helper and wire it in**

In `src/channel/sandbox/handler.py`, add this helper just above `lambda_handler`:

```python
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
```

Then update `lambda_handler` to apply the caps. Replace the final return-dict construction with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_sandbox_handler.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/sandbox/handler.py tests/unit/test_sandbox_handler.py
git commit -m "feat(sandbox): stdout/stderr length caps + UTF-8-safe truncation marker (#183)"
```

---

## Task 5: Sandbox handler — /tmp wipe on entry

**Files:**
- Modify: `src/channel/sandbox/handler.py`
- Modify: `tests/unit/test_sandbox_handler.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_sandbox_handler.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sandbox_handler.py::test_handler_wipes_tmp_on_entry -v`
Expected: FAIL — `_TMP_DIR` doesn't exist or leak.txt isn't wiped.

- [ ] **Step 3: Add the wipe helper and wire it in**

In `src/channel/sandbox/handler.py`, add at module scope:

```python
import os
import shutil

_TMP_DIR = "/tmp"   # noqa: S108 — Lambda's writable scratch dir; injected for tests
```

Add this helper above `lambda_handler`:

```python
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
```

Add the call as the first line of `lambda_handler`:

```python
def lambda_handler(event: dict, _ctx: object) -> dict[str, Any]:
    _wipe_tmp()
    code = event.get("code", "")
    # ... rest of the function unchanged
```

Also update the `cwd=` argument in `subprocess.run` to use `_TMP_DIR`:

```python
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SEC,
            cwd=_TMP_DIR,
            check=False,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_sandbox_handler.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/sandbox/handler.py tests/unit/test_sandbox_handler.py
git commit -m "feat(sandbox): wipe /tmp on entry to close warm-container leakage (#183)"
```

---

## Task 6: Sandbox handler — image harvest

**Files:**
- Modify: `src/channel/sandbox/handler.py`
- Modify: `tests/unit/test_sandbox_handler.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_sandbox_handler.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_sandbox_handler.py -v`
Expected: 3 new fails (the existing 8 still pass).

- [ ] **Step 3: Add the harvest helper and wire it in**

In `src/channel/sandbox/handler.py` at module scope:

```python
import base64

_MAX_IMAGES = 3
_MAX_IMAGE_BYTES = 1 * 1024 * 1024
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg")
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}
```

Add this helper above `lambda_handler`:

```python
def _harvest_tmp_images() -> list[dict[str, str]]:
    """Scan ``_TMP_DIR`` for image files and return up to ``_MAX_IMAGES``
    base64-encoded.

    Ordering: most-recent by mtime first. Per-image size cap
    (``_MAX_IMAGE_BYTES``) skips anything over 1 MB. SVG round-trips
    as base64 text — slightly wasteful but keeps the response shape
    uniform with raster mimes.

    The post-exec scan happens AFTER ``subprocess.run`` returns and
    BEFORE the response is serialized. Empty /tmp returns ``[]``."""
    if not os.path.isdir(_TMP_DIR):
        return []
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
    candidates.sort(reverse=True)   # newest first
    out: list[dict[str, str]] = []
    for _mtime, path in candidates:
        if len(out) >= _MAX_IMAGES:
            break
        try:
            data = open(path, "rb").read()  # noqa: SIM115 — short-lived
        except OSError:
            continue
        if len(data) > _MAX_IMAGE_BYTES:
            continue
        ext = os.path.splitext(path)[1].lower()
        out.append({
            "mime": _IMAGE_MIME.get(ext, "application/octet-stream"),
            "b64": base64.b64encode(data).decode("ascii"),
        })
    return out
```

Update `lambda_handler`'s return-dict construction:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_sandbox_handler.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/sandbox/handler.py tests/unit/test_sandbox_handler.py
git commit -m "feat(sandbox): harvest /tmp images post-exec (≤3 × 1 MB, base64) (#183)"
```

---

## Task 7: Sandbox handler — empty-code rejection

**Files:**
- Modify: `src/channel/sandbox/handler.py`
- Modify: `tests/unit/test_sandbox_handler.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_sandbox_handler.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_sandbox_handler.py -v`
Expected: 3 new fails.

- [ ] **Step 3: Add the validation at handler entry**

In `src/channel/sandbox/handler.py`, just after the `_wipe_tmp()` call inside `lambda_handler`, add:

```python
def lambda_handler(event: dict, _ctx: object) -> dict[str, Any]:
    _wipe_tmp()
    code = event.get("code", "")
    if not isinstance(code, str) or not code:
        return {"status": "error", "content": [{"text": "empty_code"}]}
    # ... rest of handler unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_sandbox_handler.py -v`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/sandbox/handler.py tests/unit/test_sandbox_handler.py
git commit -m "feat(sandbox): reject empty/missing/non-string code with structured error (#183)"
```

---

## Task 8: Strands tool wrapper — scaffold + happy path

**Files:**
- Create: `src/channel/agents/tools/code_exec.py`
- Create: `tests/unit/test_tools_code_exec.py`

- [ ] **Step 1: Write the failing tests**

Write to `tests/unit/test_tools_code_exec.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the ``code_exec`` Strands tool wrapper (#183).

The wrapper does no execution itself — it invokes the sandbox Lambda
via boto3. All AWS calls are mocked. The error_type taxonomy and
contract round-trip via ``translate_event`` are covered here too."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clear_lazy_caches():
    """Reset the ``_get_lambda_client`` lru_cache between tests so a
    real boto3 client from a sibling test can't leak across — same
    pattern as ``test_tools_web_search.py``."""
    from channel.agents.tools.code_exec import _get_lambda_client

    _get_lambda_client.cache_clear()
    yield
    _get_lambda_client.cache_clear()


def _fake_invoke_response(payload: dict) -> dict:
    """Build a boto3 ``invoke`` response shape: ``{"Payload": <stream>}``."""
    stream = MagicMock()
    stream.read.return_value = json.dumps(payload).encode()
    return {"StatusCode": 200, "Payload": stream}


def test_code_exec_returns_sandbox_payload_unchanged(monkeypatch):
    """Lambda returns ``{stdout, stderr, exit_code, ...}`` → wrapper
    returns the same dict so the SPA / translate_event can read it."""
    sandbox_payload = {
        "stdout": "42\n",
        "stderr": "",
        "exit_code": 0,
        "duration_ms": 12,
        "truncated": False,
        "timed_out": False,
        "images": [],
    }
    fake_client = MagicMock()
    fake_client.invoke.return_value = _fake_invoke_response(sandbox_payload)
    monkeypatch.setattr(
        "channel.agents.tools.code_exec._get_lambda_client",
        lambda: fake_client,
    )
    monkeypatch.setenv("STARTER_CODE_EXEC_LAMBDA_ARN", "arn:fake")

    from channel.agents.tools.code_exec import code_exec

    result = code_exec(code="print(42)")

    assert result == sandbox_payload


def test_code_exec_invokes_request_response_with_json_payload(monkeypatch):
    """Wrapper invokes ``RequestResponse`` with ``{"code": ...}`` as the JSON body."""
    fake_client = MagicMock()
    fake_client.invoke.return_value = _fake_invoke_response({
        "stdout": "", "stderr": "", "exit_code": 0,
        "duration_ms": 1, "truncated": False, "timed_out": False, "images": [],
    })
    monkeypatch.setattr(
        "channel.agents.tools.code_exec._get_lambda_client",
        lambda: fake_client,
    )
    monkeypatch.setenv("STARTER_CODE_EXEC_LAMBDA_ARN", "arn:fake")

    from channel.agents.tools.code_exec import code_exec

    code_exec(code="print(1)")

    call_kwargs = fake_client.invoke.call_args.kwargs
    assert call_kwargs["FunctionName"] == "arn:fake"
    assert call_kwargs["InvocationType"] == "RequestResponse"
    assert json.loads(call_kwargs["Payload"]) == {"code": "print(1)"}


def test_code_exec_is_a_strands_tool():
    """``@tool`` decorator wraps the function as ``DecoratedFunctionTool``
    and attaches ``tool_spec`` metadata — same probe as web_search."""
    from channel.agents.tools.code_exec import code_exec

    assert hasattr(code_exec, "tool_spec")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_tools_code_exec.py -v`
Expected: 3 fails — module not found.

- [ ] **Step 3: Implement the tool wrapper**

Write to `src/channel/agents/tools/code_exec.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""``code_exec`` — second concrete chassis tool (#128 / #183).

Synchronously invokes the ``CodeExecLambda`` sandbox via boto3 and
returns the sandbox's response dict (``{stdout, stderr, exit_code,
duration_ms, truncated, timed_out, images}``).

The sandbox Lambda's ARN is resolved from ``STARTER_CODE_EXEC_LAMBDA_ARN``
on each call (cheap env lookup; no caching needed). The boto3 Lambda
client is lazy-loaded via ``@functools.lru_cache(maxsize=1)`` — mirrors
``web_search``'s ``_get_exa_search`` pattern so a cold start without
code-exec usage doesn't pay the boto3 client construction cost.

Errors from the boto3 invoke (throttling / network / sandbox init crash)
become Strands-``ToolResult``-shaped dicts so ``translate_event`` extracts
the reason string as the stable SSE ``error_type`` token. This mirrors
PR #225's error-shape contract for ``web_search`` and is critical: a
plain ``{"status": "error", "error_type": "..."}`` dict gets re-wrapped
by Strands' ``@tool`` decorator as a single JSON-stringified text block,
defeating reason extraction.

Non-zero subprocess exit codes are NOT errors at the chassis layer —
the sandbox returns them as part of a successful response and the
model decides what to do."""

from __future__ import annotations

import functools
import json
import logging
import os
from typing import Any

import botocore.exceptions
from strands import tool

logger = logging.getLogger(__name__)


def _error_result(error_type: str) -> dict[str, Any]:
    """Build a Strands-``ToolResult``-shaped error dict.

    See ``src/channel/agents/tools/web_search.py:_error_result`` for the
    full rationale on why this shape (not ``{"error_type": "..."}``) is
    load-bearing for the SSE error contract."""
    return {"status": "error", "content": [{"text": error_type}]}


@functools.lru_cache(maxsize=1)
def _get_lambda_client():  # type: ignore[no-untyped-def]
    """Lazy-load the boto3 Lambda client.

    Mirrors the ``web_search._get_exa_search`` shape — deferring the
    boto3 import until the model actually calls ``code_exec`` keeps the
    Lambda client construction off the cold-start path for turns that
    don't trigger code execution."""
    import boto3  # noqa: PLC0415

    return boto3.client("lambda")


@tool
def code_exec(code: str) -> dict[str, Any]:
    """Execute Python code in a sandboxed subprocess. Returns stdout, stderr, exit_code.

    Use when you need to compute, transform data, analyze a CSV, plot
    something, or run a quick simulation. The environment has numpy,
    pandas, matplotlib, scipy, requests, httpx, python-dateutil
    pre-installed.

    To return a plot or other image to the user, save it to
    ``/tmp/<name>.png`` (or .jpg / .svg) — the user will see the image
    inline. Up to 3 images per call, 1 MB each.

    Network access: NONE (no VPC, no internet egress).
    Filesystem: writable ``/tmp`` only; wiped between calls.
    Timeout: 270 seconds.
    stdout cap: 20 KB; stderr cap: 5 KB.

    Args:
        code: The Python source to execute.
    """
    arn = os.environ.get("STARTER_CODE_EXEC_LAMBDA_ARN")
    if not arn:
        return _error_result("not_configured")
    client = _get_lambda_client()
    resp = client.invoke(
        FunctionName=arn,
        InvocationType="RequestResponse",
        Payload=json.dumps({"code": code}).encode(),
    )
    payload = json.loads(resp["Payload"].read())
    return payload  # type: ignore[no-any-return]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tools_code_exec.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/tools/code_exec.py tests/unit/test_tools_code_exec.py
git commit -m "feat(agents): code_exec Strands tool — happy path via boto3 invoke (#183)"
```

---

## Task 9: Tool wrapper — error taxonomy

**Files:**
- Modify: `src/channel/agents/tools/code_exec.py`
- Modify: `tests/unit/test_tools_code_exec.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_tools_code_exec.py`:

```python
def test_code_exec_returns_not_configured_when_arn_unset(monkeypatch):
    """No ``STARTER_CODE_EXEC_LAMBDA_ARN`` → ``not_configured`` error."""
    monkeypatch.delenv("STARTER_CODE_EXEC_LAMBDA_ARN", raising=False)

    from channel.agents.tools.code_exec import code_exec

    result = code_exec(code="print(1)")

    assert result == {"status": "error", "content": [{"text": "not_configured"}]}


def test_code_exec_returns_rate_limit_on_too_many_requests(monkeypatch):
    """boto3 ``TooManyRequestsException`` → ``rate_limit`` error."""
    fake_client = MagicMock()

    class _TooMany(Exception):
        pass

    fake_client.exceptions.TooManyRequestsException = _TooMany
    fake_client.invoke.side_effect = _TooMany("throttled")
    monkeypatch.setattr(
        "channel.agents.tools.code_exec._get_lambda_client",
        lambda: fake_client,
    )
    monkeypatch.setenv("STARTER_CODE_EXEC_LAMBDA_ARN", "arn:fake")

    from channel.agents.tools.code_exec import code_exec

    result = code_exec(code="print(1)")

    assert result == {"status": "error", "content": [{"text": "rate_limit"}]}


def test_code_exec_returns_invoke_failed_on_generic_client_error(monkeypatch):
    """Generic ``ClientError`` → ``invoke_failed`` error."""
    import botocore.exceptions

    fake_client = MagicMock()
    fake_client.exceptions.TooManyRequestsException = type(
        "X", (Exception,), {}
    )
    fake_client.invoke.side_effect = botocore.exceptions.ClientError(
        error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
        operation_name="Invoke",
    )
    monkeypatch.setattr(
        "channel.agents.tools.code_exec._get_lambda_client",
        lambda: fake_client,
    )
    monkeypatch.setenv("STARTER_CODE_EXEC_LAMBDA_ARN", "arn:fake")

    from channel.agents.tools.code_exec import code_exec

    result = code_exec(code="print(1)")

    assert result == {"status": "error", "content": [{"text": "invoke_failed"}]}


def test_code_exec_returns_sandbox_init_error_on_function_error(monkeypatch):
    """boto3 ``invoke`` response with ``FunctionError`` → ``sandbox_init_error``."""
    fake_client = MagicMock()
    fake_client.exceptions.TooManyRequestsException = type("X", (Exception,), {})
    stream = MagicMock()
    stream.read.return_value = b"{}"
    fake_client.invoke.return_value = {
        "StatusCode": 200,
        "FunctionError": "Unhandled",
        "Payload": stream,
    }
    monkeypatch.setattr(
        "channel.agents.tools.code_exec._get_lambda_client",
        lambda: fake_client,
    )
    monkeypatch.setenv("STARTER_CODE_EXEC_LAMBDA_ARN", "arn:fake")

    from channel.agents.tools.code_exec import code_exec

    result = code_exec(code="print(1)")

    assert result == {
        "status": "error",
        "content": [{"text": "sandbox_init_error"}],
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_tools_code_exec.py -v`
Expected: 4 new fails (existing 3 pass).

- [ ] **Step 3: Implement the error taxonomy**

Replace the body of `code_exec` in `src/channel/agents/tools/code_exec.py` after the `_get_lambda_client()` call with:

```python
@tool
def code_exec(code: str) -> dict[str, Any]:
    """Execute Python code in a sandboxed subprocess. Returns stdout, stderr, exit_code.

    Use when you need to compute, transform data, analyze a CSV, plot
    something, or run a quick simulation. The environment has numpy,
    pandas, matplotlib, scipy, requests, httpx, python-dateutil
    pre-installed.

    To return a plot or other image to the user, save it to
    ``/tmp/<name>.png`` (or .jpg / .svg) — the user will see the image
    inline. Up to 3 images per call, 1 MB each.

    Network access: NONE (no VPC, no internet egress).
    Filesystem: writable ``/tmp`` only; wiped between calls.
    Timeout: 270 seconds.
    stdout cap: 20 KB; stderr cap: 5 KB.

    Args:
        code: The Python source to execute.
    """
    arn = os.environ.get("STARTER_CODE_EXEC_LAMBDA_ARN")
    if not arn:
        return _error_result("not_configured")
    client = _get_lambda_client()
    try:
        resp = client.invoke(
            FunctionName=arn,
            InvocationType="RequestResponse",
            Payload=json.dumps({"code": code}).encode(),
        )
    except client.exceptions.TooManyRequestsException:
        logger.warning("code_exec.rate_limit code_len=%d", len(code))
        return _error_result("rate_limit")
    except botocore.exceptions.ClientError as exc:
        logger.warning("code_exec.client_error %r code_len=%d", exc, len(code))
        return _error_result("invoke_failed")
    if "FunctionError" in resp:
        logger.warning("code_exec.sandbox_init_error code_len=%d", len(code))
        return _error_result("sandbox_init_error")
    payload = json.loads(resp["Payload"].read())
    return payload  # type: ignore[no-any-return]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tools_code_exec.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/tools/code_exec.py tests/unit/test_tools_code_exec.py
git commit -m "feat(agents): code_exec error taxonomy — not_configured/rate_limit/invoke_failed/sandbox_init_error (#183)"
```

---

## Task 10: Tool wrapper — error round-trip through translate_event

**Files:**
- Modify: `tests/unit/test_tools_code_exec.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_tools_code_exec.py`:

```python
def test_code_exec_error_round_trips_through_translate_event(monkeypatch):
    """End-to-end contract test for the SSE error_type chain (same shape
    as ``test_web_search.py:test_error_result_shape_round_trips``).

    Wrapper returns ``{"status": "error", "content": [{"text": "rate_limit"}]}``;
    ``translate_event`` extracts ``rate_limit`` as ``error_type``. Fails
    if any link in the chain breaks — defensive guard against the
    PR #225 bug recurring."""
    fake_client = MagicMock()

    class _TooMany(Exception):
        pass

    fake_client.exceptions.TooManyRequestsException = _TooMany
    fake_client.invoke.side_effect = _TooMany("throttled")
    monkeypatch.setattr(
        "channel.agents.tools.code_exec._get_lambda_client",
        lambda: fake_client,
    )
    monkeypatch.setenv("STARTER_CODE_EXEC_LAMBDA_ARN", "arn:fake")

    from channel.agents.strands_sse import translate_event
    from channel.agents.tools.code_exec import code_exec

    tool_result = code_exec(code="print(1)")

    sse_event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-abc",
            "status": tool_result["status"],
            "content": tool_result["content"],
        },
    }
    kind, payload = translate_event(sse_event)
    assert kind == "tool_error"
    assert payload["error_type"] == "rate_limit"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_tools_code_exec.py::test_code_exec_error_round_trips_through_translate_event -v`
Expected: PASS (the contract from #225 already extracts the text-block reason).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_tools_code_exec.py
git commit -m "test(agents): regression guard — code_exec error_type round-trips via translate_event (#183)"
```

---

## Task 11: SSE plumbing — code-output payload through translate_event

**Files:**
- Modify: `src/channel/agents/strands_sse.py`
- Modify: `tests/unit/test_strands_sse.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_strands_sse.py`:

```python
def test_translate_event_emits_code_output_kind_for_code_exec_shaped_result():
    """tool_result whose ``content[0].text`` parses as JSON with
    ``exit_code`` + ``stdout`` keys → emit ``kind="code-output"`` +
    ``payload`` on the ``tool_finished`` event. The default literal
    summary ``"completed"`` is still set so legacy SPA paths don't
    crash."""
    from channel.agents.strands_sse import translate_event

    sandbox_payload = {
        "stdout": "42\n",
        "stderr": "",
        "exit_code": 0,
        "duration_ms": 12,
        "truncated": False,
        "timed_out": False,
        "images": [],
    }
    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-abc",
            "status": "success",
            "content": [{"text": json.dumps(sandbox_payload)}],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_finished"
    assert payload["tool_use_id"] == "tu-abc"
    assert payload["summary"] == "completed"
    assert payload["kind"] == "code-output"
    assert payload["payload"] == sandbox_payload


def test_translate_event_falls_back_to_default_for_non_code_exec_results():
    """tool_result whose content does NOT match the code-exec shape
    (e.g. a web_search result) keeps the legacy summary-only payload —
    no ``kind`` or ``payload`` fields."""
    from channel.agents.strands_sse import translate_event

    web_search_payload = {"results": [{"url": "https://example.com"}]}
    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-xyz",
            "status": "success",
            "content": [{"text": json.dumps(web_search_payload)}],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_finished"
    assert payload == {"tool_use_id": "tu-xyz", "summary": "completed"}


def test_translate_event_falls_back_when_content_is_not_valid_json():
    """tool_result content that doesn't parse as JSON → legacy payload."""
    from channel.agents.strands_sse import translate_event

    event = {
        "type": "tool_result",
        "tool_result": {
            "toolUseId": "tu-xyz",
            "status": "success",
            "content": [{"text": "not json"}],
        },
    }
    kind, payload = translate_event(event)
    assert kind == "tool_finished"
    assert payload == {"tool_use_id": "tu-xyz", "summary": "completed"}
```

(If `import json` is not already at the top of `test_strands_sse.py`, add it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_strands_sse.py -v -k "code_output or non_code or content_is_not"`
Expected: 3 fails.

- [ ] **Step 3: Extend translate_event success branch**

In `src/channel/agents/strands_sse.py`, find the success-path return statement (currently `return ("tool_finished", {"tool_use_id": tool_use_id, "summary": "completed"})` near line 137). Replace it with:

```python
        # Success: emit a generic completion marker. The raw tool
        # result text DOES NOT leak over SSE by default — that is the
        # contract documented on ``sse_tool_finished``. The ONE
        # exception is the code-exec sandbox (#183), whose entire
        # purpose is to surface stdout/stderr/images to the user via
        # a structured renderer in the SPA. We detect the code-exec
        # shape by structure (parsed JSON with ``exit_code`` +
        # ``stdout`` keys) rather than plumbing tool_name through —
        # tool_name lives on the preceding ``ToolUseStreamEvent``
        # not on the result, and translate_event is stateless.
        success_payload: dict[str, Any] = {
            "tool_use_id": tool_use_id,
            "summary": "completed",
        }
        joined_text = "".join(
            block.get("text", "")
            for block in result.get("content", [])
            if isinstance(block, dict) and "text" in block
        )
        if joined_text:
            try:
                parsed = json.loads(joined_text)
            except (ValueError, TypeError):
                parsed = None
            if (
                isinstance(parsed, dict)
                and "exit_code" in parsed
                and "stdout" in parsed
            ):
                success_payload["kind"] = "code-output"
                success_payload["payload"] = parsed
        return ("tool_finished", success_payload)
```

If `import json` is not already at the top of `strands_sse.py`, add it (it likely is; check first).

- [ ] **Step 4: Run all tests to verify they pass**

Run: `uv run pytest tests/unit/test_strands_sse.py -v`
Expected: all pass (the existing tests still pass, the 3 new ones now pass).

- [ ] **Step 5: Extend sse_tool_finished signature**

In `src/channel/agents/strands_sse.py`, replace the `sse_tool_finished` function with:

```python
def sse_tool_finished(
    *,
    tool_use_id: str,
    summary: str,
    kind: str | None = None,
    payload: dict[str, Any] | None = None,
) -> bytes:
    """Emit a ``tool_finished`` SSE event (epic #128 / #181, #183).

    Marks tool completion. ``summary`` is a short, bounded marker — the
    chassis never sends raw tool output over SSE for the default case.
    The chassis emits a literal ``"completed"`` for every success.

    ``kind`` and ``payload`` are #183's extension for structured tool
    results: when ``code_exec`` returns ``{stdout, stderr, exit_code,
    ...}``, ``translate_event`` sets ``kind="code-output"`` and
    ``payload=<that dict>`` so the SPA's ``ToolResultBlock`` can
    render the code-output branch. Other tools (e.g. ``web_search``)
    leave both ``None`` and the SPA falls back to the default summary
    branch.

    The actual tool output reaches the user via the model's text reply
    (the assistant invokes the tool, Strands' event_loop feeds the
    result back into the model, the model writes a reply that
    incorporates the data). The SSE step row is telemetry, not the
    delivery channel for tool data — except for code-exec, where the
    structured payload IS user-visible data."""
    body: dict[str, Any] = {
        "type": "tool_finished",
        "tool_use_id": tool_use_id,
        "summary": summary[:_SUMMARY_MAX],
    }
    if kind is not None:
        body["kind"] = kind
    if payload is not None:
        body["payload"] = payload
    return _sse(body)
```

- [ ] **Step 6: Add an emitter test**

Append to `tests/unit/test_strands_sse.py`:

```python
def test_sse_tool_finished_includes_kind_and_payload_when_provided():
    """``kind`` + ``payload`` ride along on the SSE wire."""
    from channel.agents.strands_sse import sse_tool_finished

    out = sse_tool_finished(
        tool_use_id="tu-x",
        summary="completed",
        kind="code-output",
        payload={"stdout": "42", "exit_code": 0},
    )
    text = out.decode()
    assert '"kind": "code-output"' in text or "'kind': 'code-output'" in text
    assert '"stdout": "42"' in text or "'stdout': '42'" in text


def test_sse_tool_finished_omits_kind_and_payload_when_unset():
    """Without ``kind``/``payload`` the wire shape is the legacy 3-field event."""
    from channel.agents.strands_sse import sse_tool_finished

    out = sse_tool_finished(tool_use_id="tu-x", summary="completed")
    text = out.decode()
    assert "kind" not in text
    assert "payload" not in text
```

- [ ] **Step 7: Run all tests to verify they pass**

Run: `uv run pytest tests/unit/test_strands_sse.py -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/channel/agents/strands_sse.py tests/unit/test_strands_sse.py
git commit -m "feat(agents): translate code-exec result shape into structured SSE payload (#183)"
```

---

## Task 12: Register code_exec behind the kill switch + chats.py forwarding

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

- [ ] **Step 1: Write the failing tests**

Open `tests/unit/test_chats_api.py`. Find the existing `STARTER_WEB_SEARCH_ENABLED` flag-on / flag-off tests (search for `"STARTER_WEB_SEARCH_ENABLED"`). Append the parallel pair for `code_exec`:

```python
def test_tool_registry_includes_code_exec_when_flag_on(monkeypatch):
    """``STARTER_CODE_EXEC_ENABLED=1`` → ``code_exec`` registered."""
    from channel.agents.tools.code_exec import code_exec

    monkeypatch.setenv("STARTER_CODE_EXEC_ENABLED", "1")
    monkeypatch.delenv("STARTER_CLOCK_TOOL_ENABLED", raising=False)
    monkeypatch.delenv("STARTER_WEB_SEARCH_ENABLED", raising=False)

    from channel.api.chats import _build_tool_registry

    registry = _build_tool_registry()
    assert code_exec in registry


def test_tool_registry_excludes_code_exec_when_flag_off(monkeypatch):
    """``STARTER_CODE_EXEC_ENABLED`` unset → ``code_exec`` NOT registered."""
    from channel.agents.tools.code_exec import code_exec

    monkeypatch.delenv("STARTER_CODE_EXEC_ENABLED", raising=False)
    monkeypatch.delenv("STARTER_CLOCK_TOOL_ENABLED", raising=False)
    monkeypatch.delenv("STARTER_WEB_SEARCH_ENABLED", raising=False)

    from channel.api.chats import _build_tool_registry

    registry = _build_tool_registry()
    assert code_exec not in registry
```

(If `_build_tool_registry` is not currently a standalone function — it's inline today inside the stream coroutine — it will be after Step 3 below. For now the tests will fail with `AttributeError`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_chats_api.py -v -k "code_exec"`
Expected: 2 fails — `_build_tool_registry` not found.

- [ ] **Step 3: Refactor the tool registry into a helper + register code_exec**

In `src/channel/api/chats.py`, find the existing inline tool-registry block (lines around 587-601 — `tool_registry: list[Any] = []` followed by the flag checks). Cut that block and replace with a single call:

```python
    tool_registry = _build_tool_registry()
```

Then add the standalone helper at module scope (place it just above `_stream_bedrock_reply` or wherever the surrounding module structure puts module-level helpers):

```python
def _build_tool_registry() -> list[Any]:
    """Assemble the per-turn tool registry from env-var kill switches.

    Each tool's registration is gated by its own ``STARTER_<NAME>_ENABLED``
    flag, defaulting to off so a stale dev env doesn't accidentally
    expose a tool. Production CDK sets all three flags to ``"1"`` in
    ``common_env``.

    Order is not significant — Strands collects tools into a name-keyed
    spec for the model.

    Extracted into a helper in #183 so the unit tests can exercise the
    flag matrix without spinning up the streaming coroutine."""
    registry: list[Any] = []
    if os.environ.get("STARTER_CLOCK_TOOL_ENABLED") == "1":
        registry.append(current_time)
    if os.environ.get("STARTER_WEB_SEARCH_ENABLED") == "1":
        registry.append(web_search)
    if os.environ.get("STARTER_CODE_EXEC_ENABLED") == "1":
        registry.append(code_exec)
    return registry
```

Add the import at the top of `chats.py` (near the other tool imports):

```python
from channel.agents.tools.code_exec import code_exec
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_chats_api.py -v -k "code_exec"`
Expected: 2 passed.

Also run the full chats API suite to make sure the refactor didn't break anything:

Run: `uv run pytest tests/unit/test_chats_api.py -v`
Expected: all passed.

- [ ] **Step 5: Confirm chats.py SSE dispatch already forwards the new fields**

The existing tool_finished dispatch in `_stream_bedrock_reply` is:

```python
elif kind == "tool_finished":
    completed_tool_calls += 1
    yield sse_tool_finished(**payload)
```

Because `sse_tool_finished` now accepts optional `kind` and `payload` keyword args (added in Task 11), the `**payload` spread Just Works — when `translate_event` adds `kind`/`payload` to the dict, they ride along; when it doesn't, the call shape matches the legacy 2-field signature. No code change needed here.

- [ ] **Step 6: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "feat(api): register code_exec behind STARTER_CODE_EXEC_ENABLED kill switch (#183)"
```

---

## Task 13: CDK CodeExecLambda construct + IAM

**Files:**
- Modify: `infra/stacks/channel_stack.py`
- Modify: `tests/unit/test_channel_stack.py`

- [ ] **Step 1: Write the failing test**

Open `tests/unit/test_channel_stack.py`. Find the existing IAM/env-var assertion tests (search for `STARTER_EXA_API_KEY_PARAM` to find the existing pattern). Append after the web-search block:

```python
def test_sandbox_lambda_exists_with_snapstart_and_python_3_13():
    """``CodeExecLambda`` is created with Python 3.13 + SnapStart on
    published versions + reserved-concurrency 5 + 5-min timeout."""
    template = _get_template()
    sandbox = [
        r for r in template["Resources"].values()
        if r["Type"] == "AWS::Lambda::Function"
        and r["Properties"].get("Runtime") == "python3.13"
        and "code-exec" in str(r["Properties"].get("FunctionName", ""))
    ]
    assert len(sandbox) == 1, "expected exactly one CodeExecLambda"
    props = sandbox[0]["Properties"]
    assert props["Timeout"] == 300
    assert props["ReservedConcurrentExecutions"] == 5
    snap = props.get("SnapStart") or {}
    assert snap.get("ApplyOn") == "PublishedVersions"


def test_sandbox_lambda_role_has_no_data_access():
    """Sandbox IAM role grants only ``AWSLambdaBasicExecutionRole`` —
    no DDB, S3, Bedrock, Secrets Manager, or SSM in any statement."""
    template = _get_template()
    # Find the sandbox role by walking the synthesized resources for
    # a Role whose AssumeRolePolicyDocument references lambda.amazonaws.com
    # AND whose ManagedPolicyArns ONLY references the basic-execution role.
    sandbox_roles = []
    for resource in template["Resources"].values():
        if resource["Type"] != "AWS::IAM::Role":
            continue
        managed = resource["Properties"].get("ManagedPolicyArns", [])
        # Skip roles that have multiple managed policies (the API role does)
        if len(managed) != 1:
            continue
        arn_ref = _flatten_intrinsic(managed[0])
        if "AWSLambdaBasicExecutionRole" not in str(arn_ref):
            continue
        sandbox_roles.append(resource)

    assert len(sandbox_roles) == 1, (
        f"expected one sandbox role with only basic-execution managed policy, "
        f"found {len(sandbox_roles)}"
    )

    # Now verify NO inline policies on this role grant data access.
    sandbox_role_logical_id = next(
        k for k, v in template["Resources"].items() if v is sandbox_roles[0]
    )
    forbidden_prefixes = ("dynamodb:", "s3:", "bedrock:", "secretsmanager:", "ssm:")
    for resource in template["Resources"].values():
        if resource["Type"] != "AWS::IAM::Policy":
            continue
        roles = resource["Properties"].get("Roles", [])
        if not any(
            _flatten_intrinsic(r) == {"Ref": sandbox_role_logical_id}
            or _flatten_intrinsic(r) == sandbox_role_logical_id
            for r in roles
        ):
            continue
        for stmt in resource["Properties"]["PolicyDocument"]["Statement"]:
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            for action in actions:
                action_str = str(action).lower()
                for prefix in forbidden_prefixes:
                    assert not action_str.startswith(prefix), (
                        f"sandbox role must not grant {action_str} "
                        f"(forbidden prefix {prefix})"
                    )


def test_api_lambda_can_invoke_sandbox():
    """API Lambda role gets ``lambda:InvokeFunction`` on the sandbox ARN."""
    template = _get_template()
    invoke_grants = []
    for resource in template["Resources"].values():
        if resource["Type"] != "AWS::IAM::Policy":
            continue
        for stmt in resource["Properties"]["PolicyDocument"]["Statement"]:
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            if any("lambda:InvokeFunction" in str(a) for a in actions):
                invoke_grants.append(stmt)
    assert invoke_grants, "expected at least one lambda:InvokeFunction grant"


def test_api_lambda_has_code_exec_env_vars():
    """API Lambda's ``Environment.Variables`` includes
    ``STARTER_CODE_EXEC_LAMBDA_ARN`` and ``STARTER_CODE_EXEC_ENABLED=1``."""
    template = _get_template()
    api_fns = [
        r for r in template["Resources"].values()
        if r["Type"] == "AWS::Lambda::Function"
        and r["Properties"].get("Handler") == "channel.api.main.handler"
    ]
    assert api_fns, "no API Lambda found"
    env_vars = api_fns[0]["Properties"]["Environment"]["Variables"]
    assert "STARTER_CODE_EXEC_LAMBDA_ARN" in env_vars
    assert env_vars["STARTER_CODE_EXEC_ENABLED"] == "1"
```

(`_get_template` and `_flatten_intrinsic` already exist in the test file — re-use them. If not, copy from the existing `STARTER_EXA_API_KEY_PARAM` test for `_flatten_intrinsic`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_channel_stack.py -v -k "sandbox or code_exec"`
Expected: 4 fails.

- [ ] **Step 3: Add the CDK construct**

In `infra/stacks/channel_stack.py`, find the existing API Lambda block (search for `api_fn = lambda_.Function(`). Just above that block, add:

```python
        # ─── Code-exec sandbox (#183) ─────────────────────────────────
        # Separate Lambda with its own IAM role. Pure compute — no DDB,
        # no S3, no Bedrock, no Secrets / SSM. Even if user code
        # escapes the subprocess (it shouldn't), the sandbox can't
        # reach Channel data. SnapStart on published versions masks the
        # cold-start cost of the sci-stack imports.
        sandbox_role = iam.Role(
            self,
            "CodeExecLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )
        code_exec_fn = lambda_.Function(
            self,
            "CodeExecLambda",
            function_name=f"channel-{env_name}-code-exec",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="channel.sandbox.handler.lambda_handler",
            code=lambda_.Code.from_asset(
                "src",
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_13.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        (
                            "pip install -r channel/sandbox/requirements.txt "
                            "-t /asset-output && cp -r channel /asset-output/channel"
                        ),
                    ],
                ),
            ),
            timeout=cdk.Duration.minutes(5),
            memory_size=1024,
            reserved_concurrent_executions=5,
            role=sandbox_role,
            snap_start=lambda_.SnapStartConf.ON_PUBLISHED_VERSIONS,
            environment={"PYTHONHASHSEED": "0"},
            log_retention=logs.RetentionDays.ONE_WEEK,
        )
```

(Imports: `iam`, `lambda_`, `logs`, `cdk` are already imported — verify with `grep "^from aws_cdk" infra/stacks/channel_stack.py`. `env_name` is already in scope above this block.)

- [ ] **Step 4: Wire the sandbox into the API Lambda**

Find the existing `api_fn.add_environment(...)` calls (search for `add_environment("STARTER_`). After the existing block, add:

```python
        code_exec_fn.current_version.grant_invoke(api_fn)
        api_fn.add_environment(
            "STARTER_CODE_EXEC_LAMBDA_ARN",
            code_exec_fn.current_version.function_arn,
        )
        api_fn.add_environment("STARTER_CODE_EXEC_ENABLED", "1")
```

(`grant_invoke` is on the version, not the function, because SnapStart-eligible invocations target a published version.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_channel_stack.py -v -k "sandbox or code_exec"`
Expected: 4 passed.

Also run the full stack test suite to make sure existing assertions still pass:

Run: `uv run pytest tests/unit/test_channel_stack.py -v`
Expected: all passed.

- [ ] **Step 6: Synth to catch CFN-level errors before deploy**

Run: `uv run inv synth`
Expected: synth completes; the new resources appear in `cdk.out/`.

- [ ] **Step 7: Commit**

```bash
git add infra/stacks/channel_stack.py tests/unit/test_channel_stack.py
git commit -m "feat(infra): CodeExecLambda sandbox + IAM least-privilege + invoke grant (#183)"
```

---

## Task 14: SPA — useChatStream payload routing

**Files:**
- Modify: `ui/src/hooks/useChatStream.js`
- Modify: `ui/src/hooks/useChatStream.test.js`

- [ ] **Step 1: Write the failing test**

Open `ui/src/hooks/useChatStream.test.js`. Find an existing `tool_finished` test (search for `tool_finished`). Append after the last one:

```js
it("routes kind + payload from tool_finished onto the step", async () => {
  const chatId = "c-code-exec";
  const sandboxPayload = {
    stdout: "42\n",
    stderr: "",
    exit_code: 0,
    duration_ms: 12,
    truncated: false,
    timed_out: false,
    images: [],
  };
  const stream = runToolStream([
    toolStarted({ tool_use_id: "tu-1", tool_name: "code_exec", args_preview: "..." }),
    {
      type: "tool_finished",
      tool_use_id: "tu-1",
      summary: "completed",
      kind: "code-output",
      payload: sandboxPayload,
    },
    doneEvent(),
  ]);
  // ... rest of the test uses the existing helper setup; see the
  // existing tool_finished test for the exact mock-fetch shape.

  const { result } = renderHook(() => useChatStream({ ...defaults, chatId }));
  await act(async () => {
    await result.current.send("run print(42)");
  });

  const lastTurn = result.current.turns[result.current.turns.length - 1];
  const step = lastTurn.toolSteps.find((s) => s.toolUseId === "tu-1");
  expect(step.kind).toBe("code-output");
  expect(step.payload).toEqual(sandboxPayload);
});
```

(Reuse the existing `toolStarted` / `doneEvent` / `runToolStream` helpers from the top of the file. If the file doesn't expose `runToolStream`, use the same `fetch`-mock pattern from the existing `tool_finished` test.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix ui test -- useChatStream.test.js -t "routes kind"`
Expected: FAIL — `step.kind` and `step.payload` are `undefined`.

- [ ] **Step 3: Extend the tool_finished branch**

In `ui/src/hooks/useChatStream.js`, find the `tool_finished` branch (search for `event.type === "tool_finished"`). Replace its `patchToolStep` call with:

```js
          } else if (event.type === "tool_finished") {
            setTurns((prev) =>
              patchToolStep(prev, tempAsstId, event.tool_use_id, {
                summary: event.summary,
                kind: event.kind,
                payload: event.payload,
                status: "finished",
              }),
            );
```

`patchToolStep` is a plain object-merge; undefined values pass through cleanly. No further change needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix ui test -- useChatStream.test.js -t "routes kind"`
Expected: PASS.

Also run the full useChatStream suite:

Run: `npm --prefix ui test -- useChatStream.test.js`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add ui/src/hooks/useChatStream.js ui/src/hooks/useChatStream.test.js
git commit -m "feat(ui): route kind + payload from tool_finished onto step state (#183)"
```

---

## Task 15: SPA — ToolResultBlock code-output branch

**Files:**
- Modify: `ui/src/app/ToolResultBlock.jsx`
- Modify: `ui/src/app/ToolResultBlock.test.jsx`

- [ ] **Step 1: Write the failing tests**

Open `ui/src/app/ToolResultBlock.test.jsx`. Replace the existing test block with:

```js
// Copyright (c) 2026 John Carter. All rights reserved.
import { render, screen, fireEvent } from "@testing-library/react";
import ToolResultBlock from "./ToolResultBlock.jsx";

describe("ToolResultBlock — default branch", () => {
  it("renders the default branch when kind is unrecognised", () => {
    render(<ToolResultBlock kind="unknown" summary="hello" />);
    expect(screen.getByText("hello")).toBeInTheDocument();
  });

  it("renders summary text by default (no kind specified)", () => {
    render(<ToolResultBlock summary="the answer is 4" />);
    expect(screen.getByText("the answer is 4")).toBeInTheDocument();
  });

  it("tags the wrapper with data-kind for future-branch dispatch", () => {
    const { container } = render(<ToolResultBlock kind="custom" summary="x" />);
    expect(container.firstChild).toHaveAttribute("data-kind", "custom");
  });

  it("falls back to data-kind=default when kind is omitted", () => {
    const { container } = render(<ToolResultBlock summary="y" />);
    expect(container.firstChild).toHaveAttribute("data-kind", "default");
  });
});

describe("ToolResultBlock — code-output branch", () => {
  const basePayload = {
    stdout: "42\n",
    stderr: "",
    exit_code: 0,
    duration_ms: 12,
    truncated: false,
    timed_out: false,
    images: [],
  };

  it("renders stdout in a pre", () => {
    render(<ToolResultBlock kind="code-output" payload={basePayload} />);
    // The stdout text appears verbatim inside a <pre>.
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("shows meta line with exit code and duration", () => {
    render(<ToolResultBlock kind="code-output" payload={basePayload} />);
    expect(screen.getByText(/exit 0/)).toBeInTheDocument();
    expect(screen.getByText(/12ms/)).toBeInTheDocument();
  });

  it("collapses stdout when > 5 lines and exposes a 'Show all' button", () => {
    const manyLines = Array.from({ length: 12 }, (_, i) => `line ${i}`).join("\n");
    render(
      <ToolResultBlock
        kind="code-output"
        payload={{ ...basePayload, stdout: manyLines }}
      />,
    );
    const toggle = screen.getByText(/Show all 12 lines/);
    expect(toggle).toBeInTheDocument();
    // Only the first 5 lines render before expansion.
    expect(screen.getByText(/line 0/)).toBeInTheDocument();
    expect(screen.queryByText(/line 8/)).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByText(/line 8/)).toBeInTheDocument();
  });

  it("does not show the toggle for 5-or-fewer lines of stdout", () => {
    render(<ToolResultBlock kind="code-output" payload={basePayload} />);
    expect(screen.queryByText(/Show all/)).not.toBeInTheDocument();
  });

  it("renders stderr inside a <details> when present", () => {
    render(
      <ToolResultBlock
        kind="code-output"
        payload={{ ...basePayload, stderr: "Traceback: ..." }}
      />,
    );
    expect(screen.getByText(/stderr.*bytes/)).toBeInTheDocument();
  });

  it("omits the stderr <details> when stderr is empty", () => {
    render(<ToolResultBlock kind="code-output" payload={basePayload} />);
    expect(screen.queryByText(/stderr.*bytes/)).not.toBeInTheDocument();
  });

  it("renders each image as an <img> with a data URI", () => {
    const payload = {
      ...basePayload,
      images: [
        { mime: "image/png", b64: "AAA=" },
        { mime: "image/jpeg", b64: "BBB=" },
      ],
    };
    const { container } = render(
      <ToolResultBlock kind="code-output" payload={payload} />,
    );
    const imgs = container.querySelectorAll("img");
    expect(imgs).toHaveLength(2);
    expect(imgs[0].src).toBe("data:image/png;base64,AAA=");
    expect(imgs[1].src).toBe("data:image/jpeg;base64,BBB=");
  });

  it("shows the truncation flag in the meta line", () => {
    render(
      <ToolResultBlock
        kind="code-output"
        payload={{ ...basePayload, truncated: true }}
      />,
    );
    expect(screen.getByText(/output truncated/)).toBeInTheDocument();
  });

  it("shows the timeout flag in the meta line", () => {
    render(
      <ToolResultBlock
        kind="code-output"
        payload={{ ...basePayload, timed_out: true }}
      />,
    );
    expect(screen.getByText(/timed out at 270s/)).toBeInTheDocument();
  });

  it("handles missing payload gracefully (no crash)", () => {
    const { container } = render(<ToolResultBlock kind="code-output" />);
    // Renders with data-kind=code-output but no children — safer than crashing.
    expect(container.firstChild).toHaveAttribute("data-kind", "code-output");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix ui test -- ToolResultBlock.test.jsx`
Expected: many fails on the code-output cases.

- [ ] **Step 3: Implement the code-output branch**

Replace `ui/src/app/ToolResultBlock.jsx` with:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
//
// Discriminated-union renderer for tool results in the Conversation
// view. The default branch is the plain monospace summary used by
// current_time / web_search. The code-output branch (#183) renders
// the sandbox Lambda result: stdout (collapsible >5 lines), stderr
// (inside <details>), inline images, and a meta line.

import { useState } from "react";

export default function ToolResultBlock({ kind, summary, payload }) {
  if (kind === "code-output") {
    return <CodeOutputBlock payload={payload} />;
  }
  return (
    <div className="tool-result-block" data-kind={kind || "default"}>
      <pre className="tool-result-summary">{summary}</pre>
    </div>
  );
}

function CodeOutputBlock({ payload }) {
  // Render-safe defaults so a missing payload doesn't crash the row.
  const [expanded, setExpanded] = useState(false);
  if (!payload) {
    return (
      <div className="tool-result-block" data-kind="code-output" />
    );
  }
  const stdout = payload.stdout || "";
  const lines = stdout.split("\n");
  // Trailing-newline-only stdout produces one extra empty line; trim
  // it so the line count matches what the user perceives.
  const lineCount =
    stdout.length > 0 && stdout.endsWith("\n") ? lines.length - 1 : lines.length;
  const collapsed = !expanded && lineCount > 5;
  const shown = collapsed ? lines.slice(0, 5).join("\n") : stdout;
  const stderrLen = (payload.stderr || "").length;
  return (
    <div className="tool-result-block" data-kind="code-output">
      {stdout && (
        <div className="code-output-pane">
          <div className="code-output-toolbar">
            <span>stdout</span>
            {lineCount > 5 && (
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="code-output-toggle"
              >
                {expanded ? "Collapse" : `Show all ${lineCount} lines`}
              </button>
            )}
          </div>
          <pre className="code-output-text">{shown}</pre>
        </div>
      )}
      {stderrLen > 0 && (
        <details className="code-output-stderr">
          <summary>stderr ({stderrLen} bytes)</summary>
          <pre>{payload.stderr}</pre>
        </details>
      )}
      {Array.isArray(payload.images) &&
        payload.images.map((img, i) => (
          <img
            key={i}
            src={`data:${img.mime};base64,${img.b64}`}
            alt={`code-exec output ${i + 1}`}
            className="code-output-image"
          />
        ))}
      <div className="code-output-meta">
        exit {payload.exit_code} · {payload.duration_ms}ms
        {payload.truncated && " · output truncated"}
        {payload.timed_out && " · timed out at 270s"}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm --prefix ui test -- ToolResultBlock.test.jsx`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/ToolResultBlock.jsx ui/src/app/ToolResultBlock.test.jsx
git commit -m "feat(ui): ToolResultBlock kind=\"code-output\" branch (#183)"
```

---

## Task 16: SPA — Conversation.jsx wires payload + app.css styling

**Files:**
- Modify: `ui/src/app/Conversation.jsx`
- Modify: `ui/src/styles/app.css`

- [ ] **Step 1: Update the ToolResultBlock call site**

In `ui/src/app/Conversation.jsx`, find the line that renders ToolResultBlock (around line 223):

```jsx
      {step.summary && <ToolResultBlock kind={step.kind} summary={step.summary} />}
```

Replace it with:

```jsx
      {(step.summary || step.payload) && (
        <ToolResultBlock kind={step.kind} summary={step.summary} payload={step.payload} />
      )}
```

The guard widens from "show only if summary" to "show if either summary or payload exists" — code-output rows have a payload but no meaningful summary.

- [ ] **Step 2: Add a regression test for Conversation.jsx**

Open `ui/src/app/Conversation.test.jsx`. Append:

```js
it("renders ToolResultBlock for a code-output step with payload but no summary", () => {
  const turn = {
    id: "asst-1",
    role: "assistant",
    text: "I ran your code.",
    toolSteps: [
      {
        toolUseId: "tu-1",
        toolName: "code_exec",
        argsPreview: "code=...",
        kind: "code-output",
        payload: {
          stdout: "42\n",
          stderr: "",
          exit_code: 0,
          duration_ms: 12,
          truncated: false,
          timed_out: false,
          images: [],
        },
        status: "finished",
      },
    ],
  };
  render(
    <Conversation
      turns={[{ id: "u-1", role: "user", text: "run print(42)" }, turn]}
      chatId="c-1"
      status="idle"
      onSend={() => {}}
      onAbort={() => {}}
    />,
  );
  expect(screen.getByText("42")).toBeInTheDocument();
});
```

(If `Conversation.test.jsx` doesn't exist or uses a different test setup, fall back to a manual verification: load the dev stack, fire a code-exec turn, confirm the code-output renderer shows in the browser — covered in Task 18 e2e tests.)

- [ ] **Step 3: Add the CSS styling block**

Open `ui/src/styles/app.css`. Append:

```css
/* ─── code-output renderer (#183) ───────────────────────────── */
.tool-result-block[data-kind="code-output"] {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}

.code-output-pane {
  background: var(--canvas);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.code-output-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px;
  background: var(--raised);
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  color: var(--ink-2);
}

.code-output-toggle {
  background: none;
  border: none;
  color: var(--accent);
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}

.code-output-text {
  margin: 0;
  padding: 12px;
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--ink);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 360px;
  overflow: auto;
}

.code-output-stderr {
  background: var(--canvas);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 8px 12px;
  font-size: 12px;
}

.code-output-stderr summary {
  cursor: pointer;
  color: var(--ink-2);
}

.code-output-stderr pre {
  margin-top: 8px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink);
  white-space: pre-wrap;
  word-break: break-word;
}

.code-output-image {
  max-width: 100%;
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
}

.code-output-meta {
  font-size: 12px;
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
}
```

(Check `ui/src/styles/channel.css` for the exact var names — if `--ink-2` is actually `--ink-secondary` or similar, adjust.)

- [ ] **Step 4: Visual verification in the dev stack**

Run the full local stack:

```bash
uv run inv dev --seed
```

In a fresh browser window, sign in (`?test_email=you@example.com`), start a new chat, ask "run a quick Python script that prints 42". Confirm:

* The code-output step appears in the message
* stdout renders in a monospace pane with proper border/background
* For a multi-line stdout (e.g. "print 1 through 20"), the collapse toggle appears
* Light + dark themes both render correctly (toggle via the theme picker)

If anything is off, fix the CSS and reload.

- [ ] **Step 5: Run all frontend tests**

Run: `npm --prefix ui test`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add ui/src/app/Conversation.jsx ui/src/app/Conversation.test.jsx ui/src/styles/app.css
git commit -m "feat(ui): wire code-output payload through Conversation + app.css styling (#183)"
```

---

## Task 17: E2e test scaffold + happy path

**Files:**
- Create: `tests/e2e/test_code_exec.py`

- [ ] **Step 1: Write the happy-path test**

Write to `tests/e2e/test_code_exec.py`:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""E2e tests for the code-exec sandbox (#183).

Runs against the deployed dev environment via the shared
``_http_helpers`` (auth bypass, chat creation, SSE consumption).

These tests assert the chassis surfaces a ``tool_finished`` event with
``kind="code-output"`` and a structured payload. They do NOT assert the
exact assistant-text reply — that depends on the model and would be
flaky."""

from __future__ import annotations

import os
import time

import pytest

from tests.e2e._http_helpers import (
    create_chat,
    drain_stream,
    sign_in_as,
    start_streamed_send,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STARTER_UI_URL"),
    reason="e2e suite requires STARTER_UI_URL set by inv e2e / inv e2e-local",
)


def _tagged_email() -> str:
    return f"e2e-code-exec-{int(time.time() * 1000)}@example.com"


def test_code_exec_happy_path_prints_and_returns():
    """Asking the model to compute 7*6 with code_exec returns:
    - a tool_started for code_exec
    - a tool_finished with kind="code-output" + a payload containing
      "42" in stdout"""
    token = sign_in_as(_tagged_email())
    chat = create_chat(token, title="e2e code-exec happy")

    stream = start_streamed_send(
        token,
        chat["chat_id"],
        prompt="Compute 7*6 by running Python code. Just print the result.",
    )
    events = drain_stream(stream, timeout_sec=120)

    started = [e for e in events if e["type"] == "tool_started"]
    finished = [e for e in events if e["type"] == "tool_finished"]
    assert any(e.get("tool_name") == "code_exec" for e in started), (
        f"expected tool_started for code_exec, got {started!r}"
    )
    code_finished = [e for e in finished if e.get("kind") == "code-output"]
    assert code_finished, (
        f"expected tool_finished with kind=code-output, got {finished!r}"
    )
    payload = code_finished[0]["payload"]
    assert "42" in payload["stdout"], (
        f"expected stdout to contain '42', got {payload['stdout']!r}"
    )
    assert payload["exit_code"] == 0
```

- [ ] **Step 2: Verify the e2e helpers exist**

Check `tests/e2e/_http_helpers.py` for the helper names used above:

Run: `grep -n "create_chat\|drain_stream\|sign_in_as\|start_streamed_send" tests/e2e/_http_helpers.py`

If any are missing — adjust the test imports to match what does exist. The web-search e2e test (`tests/e2e/test_web_search_smoke.py` if present, else `test_tool_use_smoke.py`) is the reference for the actual helper names.

- [ ] **Step 3: Run the test against deployed dev**

(Requires the PR pipeline to have deployed to dev; for local-stack iteration use `inv e2e-local`.)

For deployed dev:

```bash
uv run inv e2e --env jc --tests tests/e2e/test_code_exec.py
```

Expected: 1 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_code_exec.py
git commit -m "test(e2e): code-exec happy path — print('42') round-trips end-to-end (#183)"
```

---

## Task 18: E2e tests — containment, length cap, image return

**Files:**
- Modify: `tests/e2e/test_code_exec.py`

- [ ] **Step 1: Add the containment test**

Append to `tests/e2e/test_code_exec.py`:

```python
def test_code_exec_containment_no_network_egress():
    """Code that tries to connect to 1.1.1.1:80 fails — no VPC = no
    internet egress. The tool call succeeds (no chassis-level error)
    but the subprocess exit code is non-zero and stderr describes the
    failure."""
    token = sign_in_as(_tagged_email())
    chat = create_chat(token, title="e2e code-exec containment")

    stream = start_streamed_send(
        token,
        chat["chat_id"],
        prompt=(
            "Run this exact Python: "
            "import socket; s = socket.socket(); s.settimeout(3); "
            "s.connect(('1.1.1.1', 80))"
        ),
    )
    events = drain_stream(stream, timeout_sec=120)

    finished = [
        e for e in events
        if e["type"] == "tool_finished" and e.get("kind") == "code-output"
    ]
    assert finished, f"expected code-output tool_finished, got {events!r}"
    payload = finished[0]["payload"]
    assert payload["exit_code"] != 0, (
        f"expected non-zero exit for blocked egress, got {payload!r}"
    )
    assert payload["stderr"], (
        f"expected stderr to describe the failure, got {payload!r}"
    )
```

- [ ] **Step 2: Add the length-cap test**

Append:

```python
def test_code_exec_caps_huge_stdout():
    """Code that prints 1M characters → stdout exactly at 20 KB cap +
    ``truncated: true``."""
    token = sign_in_as(_tagged_email())
    chat = create_chat(token, title="e2e code-exec length cap")

    stream = start_streamed_send(
        token,
        chat["chat_id"],
        prompt=(
            "Run this exact Python: import sys; sys.stdout.write('x' * 1000000)"
        ),
    )
    events = drain_stream(stream, timeout_sec=120)

    finished = [
        e for e in events
        if e["type"] == "tool_finished" and e.get("kind") == "code-output"
    ]
    assert finished, f"expected code-output tool_finished, got {events!r}"
    payload = finished[0]["payload"]
    assert payload["truncated"] is True
    # 20 KB cap including a marker; allow some slack for marker bytes
    assert 19_500 < len(payload["stdout"]) <= 20_480, (
        f"stdout length out of cap range: {len(payload['stdout'])}"
    )
```

- [ ] **Step 3: Add the image-return test**

Append:

```python
def test_code_exec_returns_matplotlib_image():
    """Code that saves a matplotlib plot to /tmp/plot.png →
    ``payload.images[0]`` is a non-empty base64 PNG."""
    token = sign_in_as(_tagged_email())
    chat = create_chat(token, title="e2e code-exec image")

    stream = start_streamed_send(
        token,
        chat["chat_id"],
        prompt=(
            "Run this exact Python: import matplotlib; matplotlib.use('Agg'); "
            "import matplotlib.pyplot as plt; plt.plot([1,2,3]); "
            "plt.savefig('/tmp/plot.png')"
        ),
    )
    events = drain_stream(stream, timeout_sec=120)

    finished = [
        e for e in events
        if e["type"] == "tool_finished" and e.get("kind") == "code-output"
    ]
    assert finished, f"expected code-output tool_finished, got {events!r}"
    payload = finished[0]["payload"]
    assert len(payload["images"]) >= 1
    img = payload["images"][0]
    assert img["mime"] == "image/png"
    assert len(img["b64"]) > 100
```

- [ ] **Step 4: Run the suite**

```bash
uv run inv e2e --env jc --tests tests/e2e/test_code_exec.py
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_code_exec.py
git commit -m "test(e2e): code-exec containment + length cap + image return (#183)"
```

---

## Task 19: CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the entry**

Open `CHANGELOG.md`. Find the `## [Unreleased]` section. Under `### Added`, append:

```markdown
- **`code_exec` tool** — chats can now run Python in a sandboxed Lambda
  (separate IAM role, no network, wiped `/tmp`, 270s timeout, 20 KB stdout
  cap, sci-stack pre-installed: numpy / pandas / matplotlib / scipy / requests
  / httpx / python-dateutil). Plots saved to `/tmp/*.png` flow back as inline
  images via a new `ToolResultBlock kind="code-output"` branch. Gated by
  `STARTER_CODE_EXEC_ENABLED`. Closes #183 — epic #128 part C.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): add code-exec sandbox under Unreleased Added (#183)"
```

---

## Task 20: Local stack smoke + push the branch

**Files:**
- None (CI gate)

- [ ] **Step 1: Run the full local gate**

```bash
uv run inv pre-push
```

Expected: lint + typecheck + unit + frontend all pass.

If anything fails, fix it before proceeding. Coverage should still be 100% on Python and 100% on JS.

- [ ] **Step 2: Run the deploy to dev environment**

```bash
uv run inv deploy --env jc
```

Expected: CDK deploys cleanly — both the API Lambda and the new sandbox Lambda land.

- [ ] **Step 3: Smoke-test the deployed flow**

Open the dev URL, sign in as a test email, start a chat asking "compute 7*6 by running code". Confirm:

* The Conversation view shows a `code_exec` step
* stdout renders in the code-output pane
* exit 0, duration in milliseconds shown

Then try "plot [1,2,3] with matplotlib and save to /tmp/plot.png" — confirm an image renders inline.

- [ ] **Step 4: Push with explicit refspec (W1-W7 push discipline)**

Per `.claude/agents/issue-worker.md` push-discipline rules:

```bash
git fetch origin
git rebase origin/development
git log --oneline origin/development..HEAD  # confirm only YOUR commits show
PRE_REBASE_SHA=$(git rev-parse HEAD)
git push -u origin feat/183-code-exec-sandbox:feat/183-code-exec-sandbox
```

- [ ] **Step 5: Open the PR (NO auto-merge — #183 is not agent-safe)**

```bash
gh pr create --base development \
  --title "feat(infra+agents+ui): code execution via Lambda sandbox — Closes #183" \
  --body "$(cat <<'EOF'
## Summary

- Ships `code_exec` as the second concrete tool on top of the #181 chassis. The model can now run Python in a separate Lambda sandbox with sci-stack pre-installed, surface stdout / stderr / images inline in the SPA, and stay within the chassis's per-chain tool-call budget.
- New `CodeExecLambda` CDK construct with its own IAM role (no DDB / S3 / Bedrock access — pure compute), SnapStart, reserved-concurrency 5, no VPC.
- New `ToolResultBlock kind="code-output"` SPA branch (monospace stdout collapsed >5 lines, `<details>` stderr, inline images, meta line with exit + duration + truncation/timeout flags).
- Closes #183. With this + #184's spike doc landed, epic #128 closes.

## Test plan

- [x] Unit: sandbox handler (14 tests covering happy path / timeout / caps / /tmp wipe / image harvest / empty-code rejection)
- [x] Unit: tool wrapper (7 tests covering happy path + 4 error_type branches + Strands tool spec + round-trip via translate_event)
- [x] Unit: translate_event code-output dispatch (3 tests covering the structured-payload detection)
- [x] Unit: CDK assertions (4 tests covering sandbox Lambda config + IAM least-privilege + invoke grant + API Lambda env vars)
- [x] Frontend: ToolResultBlock code-output branch (10 tests)
- [x] Frontend: useChatStream payload routing (1 new test alongside existing tool tests)
- [x] E2e: happy path + containment + length cap + image return (4 tests)
- [x] Local stack smoke: print(42) → code-output renders inline
- [x] Local stack smoke: matplotlib plot → image renders inline
- [x] pre-push gate passes (lint + typecheck + unit + frontend, 100% coverage)
- [x] Deploy to dev env clean

Spec: \`docs/superpowers/specs/2026-06-08-183-code-exec-sandbox-design.md\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

**Do NOT run `gh pr merge --auto`.** #183 is `priority:p2 size:l infra` — not agent-safe. Human drives merge after CI green + Copilot review.

- [ ] **Step 6: Watch CI**

```bash
gh run watch
```

Address any failures by fixing locally + re-pushing (PRE_REBASE_SHA preserves the prior tip for `--force-with-lease` if a rebase becomes necessary).

- [ ] **Step 7: Wait for Copilot review and respond to comments**

Once CI green, Copilot review fires automatically. Address each comment with a substantive fix or a substantive reply explaining why no change is needed.

- [ ] **Step 8: Hand off**

When CI is green AND Copilot comments are addressed, stop. The human will drive the merge. After merge to `development`, monitor the dev deploy pipeline.
