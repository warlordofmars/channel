# Copyright (c) 2026 John Carter. All rights reserved.
"""Code-exec sandbox e2e smoke test (#183). Exercises the Lambda
sandbox end-to-end via the chassis SSE protocol.

Gated by ``STARTER_CODE_EXEC_ENABLED=1`` AND ``STARTER_CODE_EXEC_LAMBDA_ARN``
present (both are set by CDK in deployed envs; local dev without the
sandbox stack falls through the skip).

The happy-path test asks the model to compute a number via Python and
asserts the SSE stream emits ``tool_started`` + ``tool_finished`` for
``code_exec`` with ``kind="code-output"`` and a structured payload
containing the expected number in ``stdout``.

The sync HTTP helpers (mint JWT, create / delete chat, stream messages)
live in ``tests/e2e._http_helpers``.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import pytest

from tests.e2e._http_helpers import (
    _create_chat,
    _delete_chat,
    _mint_jwt_via_bypass,
    _stream_messages,
)

# Cold-start of the sandbox Lambda + subprocess invocation can take longer
# than current_time. Bump past the helper default (120 s).
_CODE_EXEC_TIMEOUT = 180.0


def _run_code_exec_chat(
    prompt: str, timeout: float = _CODE_EXEC_TIMEOUT
) -> list[dict[str, Any]]:
    """Setup → stream → cleanup boilerplate shared by the e2e tests.

    Mints a JWT, creates a chat, streams the user prompt, and returns
    the SSE event list. Always deletes the chat in finally."""
    api_url = os.environ.get("STARTER_API_URL", "http://localhost:8001")
    tag = f"e2e-183-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email = f"{tag}@example.com"
    jwt = _mint_jwt_via_bypass(api_url, email)
    chat_id = _create_chat(api_url, jwt, title=f"code-exec-{tag}")
    try:
        return _stream_messages(api_url, jwt, chat_id, prompt, timeout=timeout)
    finally:
        _delete_chat(api_url, jwt, chat_id)


@pytest.mark.skipif(
    os.environ.get("STARTER_CODE_EXEC_ENABLED") != "1"
    or not os.environ.get("STARTER_CODE_EXEC_LAMBDA_ARN"),
    reason=(
        "requires STARTER_CODE_EXEC_ENABLED=1 AND "
        "STARTER_CODE_EXEC_LAMBDA_ARN set (deployed-env config)"
    ),
)
def test_code_exec_happy_path() -> None:
    """Ask the model to compute 7*6 via code_exec; assert tool_started +
    tool_finished for code_exec, no tool_error, the tool_finished event
    has ``kind="code-output"``, and the structured payload's stdout
    contains "42".

    We do NOT assert on the assistant's text reply — the model's
    natural-language wrapping of the result varies and would make the
    test flaky. The chassis-level structured-payload assertion is the
    binding contract for this PR."""
    events = _run_code_exec_chat(
        prompt=(
            "Use the code_exec tool to compute 7 * 6. "
            "Run the exact Python: print(7*6)"
        )
    )

    started = [e for e in events if e.get("type") == "tool_started"]
    finished = [e for e in events if e.get("type") == "tool_finished"]
    errors = [e for e in events if e.get("type") == "tool_error"]

    # 1. A code_exec tool was started.
    code_exec_starts = [e for e in started if e.get("tool_name") == "code_exec"]
    assert code_exec_starts, (
        f"Expected at least one tool_started for code_exec; got "
        f"tool_started tool_names={[e.get('tool_name') for e in started]!r}"
    )

    # 2. The matching tool_finished arrived with kind="code-output"
    #    and a structured payload.
    started_ids = {e["tool_use_id"] for e in code_exec_starts}
    finished_for_code = [
        e for e in finished if e.get("tool_use_id") in started_ids
    ]
    assert finished_for_code, (
        f"No tool_finished for code_exec tool_use_id. "
        f"started_ids={started_ids!r} finished_ids="
        f"{[e.get('tool_use_id') for e in finished]!r}"
    )
    finished_event = finished_for_code[0]
    assert finished_event.get("kind") == "code-output", (
        f"Expected kind=code-output on tool_finished; got "
        f"{finished_event.get('kind')!r}. Full event: {finished_event!r}"
    )
    payload = finished_event.get("payload")
    assert isinstance(payload, dict), (
        f"Expected dict payload on code-output tool_finished; got "
        f"{type(payload).__name__}. Event: {finished_event!r}"
    )

    # 3. The structured payload contains "42" in stdout.
    stdout = payload.get("stdout", "")
    assert "42" in stdout, (
        f"Expected '42' in code_exec stdout; got stdout={stdout!r}"
    )
    assert payload.get("exit_code") == 0, (
        f"Expected exit_code=0 for successful execution; got "
        f"exit_code={payload.get('exit_code')!r}"
    )

    # 4. No tool_error for the matched tool_use_id — happy path.
    errors_for_code = [
        e for e in errors if e.get("tool_use_id") in started_ids
    ]
    assert not errors_for_code, (
        "Expected NO tool_error for code_exec's tool_use_id on the "
        f"happy path; got {errors_for_code!r}"
    )


@pytest.mark.skipif(
    os.environ.get("STARTER_CODE_EXEC_ENABLED") != "1"
    or not os.environ.get("STARTER_CODE_EXEC_LAMBDA_ARN"),
    reason=(
        "requires STARTER_CODE_EXEC_ENABLED=1 AND "
        "STARTER_CODE_EXEC_LAMBDA_ARN set (deployed-env config)"
    ),
)
def test_code_exec_containment_no_network() -> None:
    """Code that tries to connect to 1.1.1.1:80 fails — no VPC means
    no internet egress. The tool call succeeds (no chassis-level error)
    but the subprocess exit code is non-zero and stderr describes the
    failure. This is the load-bearing security assertion for the PR."""
    events = _run_code_exec_chat(
        prompt=(
            "Use the code_exec tool to run this exact Python: "
            "import socket; s = socket.socket(); s.settimeout(3); "
            "s.connect(('1.1.1.1', 80))"
        )
    )

    finished = [
        e for e in events
        if e.get("type") == "tool_finished"
        and e.get("kind") == "code-output"
    ]
    assert finished, (
        f"Expected code-output tool_finished; got {[e.get('type') for e in events]!r}"
    )
    payload = finished[0]["payload"]
    assert payload["exit_code"] != 0, (
        f"Expected non-zero exit for blocked egress; got {payload!r}"
    )
    assert payload["stderr"], (
        f"Expected stderr describing the failure; got {payload!r}"
    )


@pytest.mark.skipif(
    os.environ.get("STARTER_CODE_EXEC_ENABLED") != "1"
    or not os.environ.get("STARTER_CODE_EXEC_LAMBDA_ARN"),
    reason=(
        "requires STARTER_CODE_EXEC_ENABLED=1 AND "
        "STARTER_CODE_EXEC_LAMBDA_ARN set (deployed-env config)"
    ),
)
def test_code_exec_caps_huge_stdout() -> None:
    """Code that prints 1M characters → stdout exactly at the 20 KB cap
    with truncated=True. Validates the sandbox handler's UTF-8-safe
    truncation marker reaches the SSE wire intact."""
    events = _run_code_exec_chat(
        prompt=(
            "Use the code_exec tool to run this exact Python: "
            "import sys; sys.stdout.write('x' * 1000000)"
        )
    )

    finished = [
        e for e in events
        if e.get("type") == "tool_finished"
        and e.get("kind") == "code-output"
    ]
    assert finished, (
        f"Expected code-output tool_finished; got {[e.get('type') for e in events]!r}"
    )
    payload = finished[0]["payload"]
    assert payload["truncated"] is True, (
        f"Expected truncated=true; got payload={payload!r}"
    )
    # 20 KB cap including a marker; allow some slack for marker bytes.
    stdout_len = len(payload["stdout"])
    assert 19_500 < stdout_len <= 20_480, (
        f"stdout length out of cap range: {stdout_len} bytes"
    )


@pytest.mark.skipif(
    os.environ.get("STARTER_CODE_EXEC_ENABLED") != "1"
    or not os.environ.get("STARTER_CODE_EXEC_LAMBDA_ARN"),
    reason=(
        "requires STARTER_CODE_EXEC_ENABLED=1 AND "
        "STARTER_CODE_EXEC_LAMBDA_ARN set (deployed-env config)"
    ),
)
def test_code_exec_returns_matplotlib_image() -> None:
    """Code that saves a matplotlib plot to /tmp/plot.png → the harvest
    step encodes it into payload.images[0]. Validates the
    sci-stack-pre-installed contract + the /tmp scan + base64 round-trip."""
    events = _run_code_exec_chat(
        prompt=(
            "Use the code_exec tool to run this exact Python: "
            "import matplotlib; matplotlib.use('Agg'); "
            "import matplotlib.pyplot as plt; plt.plot([1,2,3]); "
            "plt.savefig('/tmp/plot.png')"
        )
    )

    finished = [
        e for e in events
        if e.get("type") == "tool_finished"
        and e.get("kind") == "code-output"
    ]
    assert finished, (
        f"Expected code-output tool_finished; got {[e.get('type') for e in events]!r}"
    )
    payload = finished[0]["payload"]
    assert len(payload["images"]) >= 1, (
        f"Expected at least one harvested image; got payload={payload!r}"
    )
    img = payload["images"][0]
    assert img["mime"] == "image/png", (
        f"Expected mime=image/png; got {img!r}"
    )
    assert len(img["b64"]) > 100, (
        f"Expected non-trivial base64 payload; got len={len(img['b64'])}"
    )
