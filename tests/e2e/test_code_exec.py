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
    api_url = os.environ.get("STARTER_API_URL", "http://localhost:8001")

    tag = f"e2e-183-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email = f"{tag}@example.com"
    jwt = _mint_jwt_via_bypass(api_url, email)
    chat_id = _create_chat(api_url, jwt, title=f"code-exec-smoke-{tag}")

    try:
        events = _stream_messages(
            api_url,
            jwt,
            chat_id,
            (
                "Use the code_exec tool to compute 7 * 6. "
                "Run the exact Python: print(7*6)"
            ),
            timeout=_CODE_EXEC_TIMEOUT,
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
    finally:
        _delete_chat(api_url, jwt, chat_id)
