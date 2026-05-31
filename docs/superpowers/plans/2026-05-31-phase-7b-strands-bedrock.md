# Phase 7b — Strands Agents + real Bedrock streaming

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the server-side canned reply with real Bedrock streaming via [Strands Agents](https://strandsagents.com/), keep AgentCore Memory unwired (`memory=None`) for now, and ship the model picker + `/regenerate` features the spec promised.

**Architecture:** New `src/channel/agents/chat_agent.py` constructs a `strands.Agent` per turn with `BedrockModel(model_id=...)`. A small `strands_sse.py` translator turns Strands' typed stream events into the existing `user_persisted` / `delta` / `done` SSE shapes. The chat router's `_stream_canned_reply` is renamed to `_stream_bedrock_reply` and gains the Bedrock invocation in place; everything else (idempotency, DDB writes, ownership check) stays. `inline_agent.py` + `bedrock.py` are deleted — Strands fully supersedes them.

**Tech Stack:** Strands Agents (PyPI: `strands-agents`) · boto3 / Bedrock Runtime · FastAPI · AWS CDK · pytest · React 18 · vitest

**Spec:** `docs/superpowers/specs/2026-05-30-bedrock-agentcore-chat-design.md`

**Coverage gate:** 100% on both Python (`pytest-cov`) and JS (`vitest v8`).

**Commit cadence:** One commit per task. `uv run inv pre-push` before pushing.

---

## Important codebase facts (verified before writing this plan)

1. **The project is already on `aws-lambda-web-adapter` (AWSLWA).** `run.sh` runs `uvicorn` on port 8080; AWSLWA layer is attached at `infra/stacks/channel_stack.py:247-250`; Function URL is already `InvokeMode.RESPONSE_STREAM`; `AWS_LWA_INVOKE_MODE=response_stream` env var is set. The original 7a spec's "Mangum → adapter switch" item was based on a **stale CDK comment** at `channel_stack.py:7` — no adapter switch is needed.
2. **`bedrock:InvokeInlineAgent` IAM is already granted** at `channel_stack.py:351-358`. Strands' `BedrockModel` uses `bedrock:InvokeModelWithResponseStream`, which is also already granted (line 341) — scoped to Sonnet 4.6 and Haiku 4.5 only. Opus (which `ui/src/app/data.js` advertises) is NOT granted; this plan extends the IAM.
3. **Lambda timeout is 30s** (`channel_stack.py:384`). The plan raises it to 5 minutes for streaming chats.
4. **`bedrock-runtime` model IDs are full ARNs** — `anthropic.claude-sonnet-4-6` (already in env) and `anthropic.claude-haiku-4-5-20251001-v1:0` (already IAM'd) work. Opus's full ID needs verification against `aws bedrock list-foundation-models` at implementation time — the plan uses `anthropic.claude-opus-4-7` as the placeholder; the implementer must verify the actual ID before grant.

---

## File map

**Created:**
- `src/channel/agents/chat_agent.py` — `build_agent()` factory + `stream_agent_events()` helper.
- `src/channel/agents/strands_sse.py` — Strands event → SSE byte translator.
- `tests/unit/test_chat_agent.py`
- `tests/unit/test_strands_sse.py`
- `docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md` — written during Task 15 (the spike).

**Modified:**
- `pyproject.toml` — add `strands-agents` dependency.
- `infra/stacks/channel_stack.py` — Lambda timeout 30s → 300s; extend Bedrock IAM with Opus model ARN; fix the stale "FastAPI + Mangum" comment.
- `src/channel/api/chats.py` — replace `_stream_canned_reply` with `_stream_bedrock_reply`; add `/api/models` and `/regenerate` endpoints; carry real `msg_id`s into the idempotency replay payload (deferred 7a follow-up).
- `tests/unit/test_chats_api.py` — new tests for `/models`, `/regenerate`, real Bedrock streaming (with mocked Strands agent), and the strengthened idempotency payload.
- `ui/src/api.js` — add `listModels()` and `regenerate()` wrappers.
- `ui/src/api.test.js` — tests for the two new wrappers.
- `ui/src/app/data.js` — keep `MODELS` as **display metadata**; remove the hardcoded id list (now sourced from `/api/models`).
- `ui/src/app/ModelPicker.jsx` — fetch valid models from API; merge with display metadata.
- `ui/src/app/ModelPicker.test.jsx`
- `ui/src/hooks/useChatStream.js` — add `regenerate()` method.
- `ui/src/hooks/useChatStream.test.js` — regenerate path tests.
- `ui/src/app/Conversation.jsx` — wire regenerate button + format the `model` field for display (fixes the 7a "raw model id in header" regression).
- `ui/src/app/Conversation.test.jsx` — regenerate + model-label tests.

**Deleted:**
- `src/channel/agents/inline_agent.py`
- `src/channel/agents/bedrock.py`
- `tests/unit/test_agents_inline_agent.py`
- `tests/unit/test_agents_bedrock.py`

---

## Task 1: Add `strands-agents` dependency and verify import surface

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock` (regenerated)

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml`. Find the `dependencies = [...]` block and add `"strands-agents>=1.0.0,<2",` (alphabetical placement — likely between `starlette` and `tomli` or wherever it fits the existing order). Do NOT pin to an exact version; cap at the next major.

- [ ] **Step 2: Lock**

```bash
uv lock
```

Expected: `uv.lock` updated with strands-agents and its transitive deps. `git diff uv.lock` should show only additive entries.

- [ ] **Step 3: Verify imports**

```bash
uv run python -c "from strands import Agent; from strands.models import BedrockModel; print('OK', Agent, BedrockModel)"
```

Expected: `OK <class 'strands.agent.Agent'> <class 'strands.models.bedrock.BedrockModel'>`. If the import path differs (Strands has reorganised across minor versions), record the actual paths — every subsequent task uses them.

- [ ] **Step 4: Sanity-run the existing suite**

```bash
uv run inv test-unit
```

Expected: 300+ tests still pass (no test should break just from a new dep landing).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(channel-7b): add strands-agents dependency"
```

---

## Task 2: SSE event translator — `strands_sse.py`

**Files:**
- Create: `src/channel/agents/strands_sse.py`
- Create: `tests/unit/test_strands_sse.py`

This module translates the Strands stream event objects into our existing SSE byte format (`data: {json}\n\n`). It's pure — no Strands `Agent` invocation, no I/O. Keeping the translator in its own file means the chat router never has to know Strands' event shape.

The Strands stream surface (verified at Task 1 import time): `agent.stream_async(prompt)` yields dicts with shapes like:
- `{"event": {"contentBlockDelta": {"delta": {"text": "..."}}}}` — incremental text
- `{"event": {"messageStop": {"stopReason": "end_turn"}}}` — turn ended
- `{"event": {"metadata": {"usage": {"inputTokens": N, "outputTokens": M}, "metrics": {...}}}}` — usage report
- `{"data": "..."}` (some versions emit this as a top-level shortcut for text-deltas)

The translator pattern-matches all four shapes and emits a single normalised event type to the caller — `("delta", str)`, `("stop", {stop_reason, input_tokens, output_tokens})`, or `("skip", None)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_strands_sse.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the Strands event → SSE translator."""

from __future__ import annotations

import json

from channel.agents.strands_sse import (
    sse_delta,
    sse_done,
    sse_user_persisted,
    translate_event,
)


def test_translate_content_block_delta_returns_delta():
    event = {"event": {"contentBlockDelta": {"delta": {"text": "Hello"}}}}
    kind, payload = translate_event(event)
    assert kind == "delta"
    assert payload == "Hello"


def test_translate_data_shortcut_returns_delta():
    event = {"data": "world"}
    kind, payload = translate_event(event)
    assert kind == "delta"
    assert payload == "world"


def test_translate_message_stop_returns_stop():
    event = {"event": {"messageStop": {"stopReason": "end_turn"}}}
    kind, payload = translate_event(event)
    assert kind == "stop"
    assert payload == {"stop_reason": "end_turn"}


def test_translate_metadata_returns_usage():
    event = {
        "event": {
            "metadata": {
                "usage": {"inputTokens": 12, "outputTokens": 8},
                "metrics": {"latencyMs": 42},
            }
        }
    }
    kind, payload = translate_event(event)
    assert kind == "usage"
    assert payload == {"input_tokens": 12, "output_tokens": 8}


def test_translate_unknown_event_returns_skip():
    event = {"event": {"contentBlockStart": {}}}
    kind, payload = translate_event(event)
    assert kind == "skip"
    assert payload is None


def test_sse_delta_emits_bytes_with_data_prefix():
    raw = sse_delta("Hello")
    assert raw.startswith(b"data: ")
    assert raw.endswith(b"\n\n")
    payload = json.loads(raw[6:-2])
    assert payload == {"type": "delta", "text": "Hello"}


def test_sse_done_includes_usage_and_metadata():
    raw = sse_done(
        msg_id="m-1",
        seq=1,
        model="anthropic.claude-sonnet-4-6",
        input_tokens=12,
        output_tokens=8,
        stop_reason="end_turn",
    )
    payload = json.loads(raw[6:-2])
    assert payload == {
        "type": "done",
        "msg_id": "m-1",
        "seq": 1,
        "model": "anthropic.claude-sonnet-4-6",
        "input_tokens": 12,
        "output_tokens": 8,
        "stop_reason": "end_turn",
    }


def test_sse_user_persisted_includes_msg_id_and_seq():
    raw = sse_user_persisted(msg_id="u-1", seq=0)
    payload = json.loads(raw[6:-2])
    assert payload == {"type": "user_persisted", "msg_id": "u-1", "seq": 0}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_strands_sse.py -v
```

Expected: `ModuleNotFoundError: No module named 'channel.agents.strands_sse'`.

- [ ] **Step 3: Implement the translator**

```python
# src/channel/agents/strands_sse.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Strands stream event → SSE byte translator.

The chat router consumes `strands.Agent.stream_async()` events and
must emit our existing SSE shapes (``user_persisted`` / ``delta`` /
``done``) that the SPA's ``useChatStream`` hook already understands.

This module is pure: no I/O, no Strands invocation.  The router calls
``translate_event`` per Strands event then ``sse_delta`` / ``sse_done``
to render the byte form.
"""

from __future__ import annotations

import json
from typing import Any


def translate_event(event: dict[str, Any]) -> tuple[str, Any]:
    """Classify one Strands stream event.

    Returns a ``(kind, payload)`` tuple where ``kind`` is one of:

    * ``"delta"`` — incremental text chunk; ``payload`` is a ``str``.
    * ``"stop"`` — turn boundary; ``payload`` is ``{"stop_reason": str}``.
    * ``"usage"`` — token-count metadata; ``payload`` is
      ``{"input_tokens": int, "output_tokens": int}``.
    * ``"skip"`` — uninteresting event (content block start, telemetry,
      etc.); ``payload`` is ``None``.
    """

    if "data" in event and isinstance(event["data"], str):
        return ("delta", event["data"])

    inner = event.get("event") or {}

    if "contentBlockDelta" in inner:
        text = inner["contentBlockDelta"].get("delta", {}).get("text")
        if text:
            return ("delta", text)

    if "messageStop" in inner:
        return ("stop", {"stop_reason": inner["messageStop"].get("stopReason", "end_turn")})

    if "metadata" in inner:
        usage = inner["metadata"].get("usage") or {}
        return (
            "usage",
            {
                "input_tokens": int(usage.get("inputTokens", 0)),
                "output_tokens": int(usage.get("outputTokens", 0)),
            },
        )

    return ("skip", None)


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def sse_user_persisted(*, msg_id: str, seq: int) -> bytes:
    return _sse({"type": "user_persisted", "msg_id": msg_id, "seq": seq})


def sse_delta(text: str) -> bytes:
    return _sse({"type": "delta", "text": text})


def sse_done(
    *,
    msg_id: str,
    seq: int,
    model: str,
    input_tokens: int,
    output_tokens: int,
    stop_reason: str,
) -> bytes:
    return _sse(
        {
            "type": "done",
            "msg_id": msg_id,
            "seq": seq,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "stop_reason": stop_reason,
        }
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_strands_sse.py -v
```

Expected: 8/8 tests pass.

- [ ] **Step 5: Coverage check**

```bash
uv run pytest tests/unit/test_strands_sse.py --cov=channel.agents.strands_sse --cov-report=term-missing
```

Expected: 100%.

- [ ] **Step 6: Commit**

```bash
git add src/channel/agents/strands_sse.py tests/unit/test_strands_sse.py
git commit -m "feat(channel-7b): Strands event → SSE translator"
```

---

## Task 3: `chat_agent.py` — Strands `Agent` factory

**Files:**
- Create: `src/channel/agents/chat_agent.py`
- Create: `tests/unit/test_chat_agent.py`

The factory builds a `strands.Agent` per turn with `BedrockModel(model_id=...)`, a system prompt, and `memory=None` (Strands' AgentCore Memory adapter is wired in 7c). The agent is constructed fresh per turn (Strands agents are lightweight config wrappers, not long-lived state).

The factory does NOT invoke the model — that's the router's job. It just returns a constructed `Agent` instance.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chat_agent.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Unit tests for the Strands Agent factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from channel.agents.chat_agent import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_SYSTEM_PROMPT,
    build_agent,
    resolve_model_id,
)


def test_resolve_model_id_returns_full_bedrock_id_for_known_id():
    assert resolve_model_id("claude-sonnet-4-6") == "anthropic.claude-sonnet-4-6"


def test_resolve_model_id_returns_full_bedrock_id_for_haiku():
    assert resolve_model_id("claude-haiku-4-5") == "anthropic.claude-haiku-4-5-20251001-v1:0"


def test_resolve_model_id_raises_on_unknown_model():
    with pytest.raises(ValueError, match="unknown model"):
        resolve_model_id("claude-opus-4-99-fictional")


def test_default_system_prompt_is_non_empty_string():
    assert isinstance(DEFAULT_SYSTEM_PROMPT, str)
    assert len(DEFAULT_SYSTEM_PROMPT) > 0


def test_default_max_tokens_is_positive_int():
    assert isinstance(DEFAULT_MAX_TOKENS, int) and DEFAULT_MAX_TOKENS > 0


def test_build_agent_constructs_strands_agent_with_bedrock_model(monkeypatch):
    captured = {}

    class FakeBedrockModel:
        def __init__(self, model_id, **kwargs):
            captured["model_id"] = model_id
            captured["kwargs"] = kwargs

    class FakeAgent:
        def __init__(self, model, system_prompt=None, **kwargs):
            captured["agent_model"] = model
            captured["system_prompt"] = system_prompt
            captured["agent_kwargs"] = kwargs

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", FakeBedrockModel)
    monkeypatch.setattr("channel.agents.chat_agent.Agent", FakeAgent)

    agent = build_agent(model_id="claude-sonnet-4-6")

    assert isinstance(agent, FakeAgent)
    assert captured["model_id"] == "anthropic.claude-sonnet-4-6"
    assert captured["system_prompt"] == DEFAULT_SYSTEM_PROMPT
    # memory adapter is None in 7b — wired in 7c
    assert captured["agent_kwargs"].get("memory") is None


def test_build_agent_uses_custom_system_prompt_when_provided(monkeypatch):
    captured = {}

    monkeypatch.setattr("channel.agents.chat_agent.BedrockModel", lambda **_: object())
    monkeypatch.setattr(
        "channel.agents.chat_agent.Agent",
        lambda model, system_prompt=None, **kw: captured.update(
            {"system_prompt": system_prompt}
        ),
    )

    build_agent(model_id="claude-sonnet-4-6", system_prompt="Be brief.")
    assert captured["system_prompt"] == "Be brief."
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_chat_agent.py -v
```

Expected: `ModuleNotFoundError: No module named 'channel.agents.chat_agent'`.

- [ ] **Step 3: Implement the factory**

```python
# src/channel/agents/chat_agent.py
# Copyright (c) 2026 John Carter. All rights reserved.
"""Strands ``Agent`` factory for the chat router.

The factory constructs a fresh ``strands.Agent`` per turn with a
``BedrockModel`` and an optional system prompt.  Memory adapter is
``None`` in 7b — 7c wires the AgentCore Memory adapter once the spike
finalises the recall semantics.
"""

from __future__ import annotations

from typing import Any

from strands import Agent
from strands.models import BedrockModel

# Caller-supplied short ids (``claude-sonnet-4-6``) → full Bedrock model
# ARNs. The CDK stack at ``infra/stacks/channel_stack.py:341-345`` must
# grant ``bedrock:InvokeModelWithResponseStream`` on every entry below.
_MODEL_ID_MAP = {
    "claude-sonnet-4-6": "anthropic.claude-sonnet-4-6",
    "claude-haiku-4-5": "anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-opus-4-7": "anthropic.claude-opus-4-7",
}

DEFAULT_MAX_TOKENS = 4096

DEFAULT_SYSTEM_PROMPT = (
    "You are Channel, a helpful AI assistant.  Be concise, accurate, "
    "and tailored to the user's apparent expertise.  Use markdown for "
    "structure (lists, headings, code blocks) when it aids clarity."
)


def resolve_model_id(short_id: str) -> str:
    """Map a caller-supplied short id to a full Bedrock model ARN."""

    try:
        return _MODEL_ID_MAP[short_id]
    except KeyError as exc:
        raise ValueError(f"unknown model: {short_id!r}") from exc


def build_agent(
    *,
    model_id: str,
    system_prompt: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Agent:
    """Construct a fresh Strands ``Agent`` for one chat turn."""

    bedrock = BedrockModel(
        model_id=resolve_model_id(model_id),
        max_tokens=max_tokens,
    )
    return Agent(
        model=bedrock,
        system_prompt=system_prompt or DEFAULT_SYSTEM_PROMPT,
        memory=None,
    )
```

> **Verification note:** if Strands' `Agent` doesn't accept `memory=None` as a kwarg in the installed version (Task 1 verified the import paths but not every kwarg), drop the line — the default is no memory. Update the test's `agent_kwargs.get("memory") is None` assertion accordingly: change to `assert "memory" not in captured["agent_kwargs"]`. The functional result is identical.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_chat_agent.py -v
```

Expected: 7/7 tests pass.

- [ ] **Step 5: Coverage check**

```bash
uv run pytest tests/unit/test_chat_agent.py --cov=channel.agents.chat_agent --cov-report=term-missing
```

Expected: 100% on `channel.agents.chat_agent`.

- [ ] **Step 6: Commit**

```bash
git add src/channel/agents/chat_agent.py tests/unit/test_chat_agent.py
git commit -m "feat(channel-7b): Strands Agent factory with BedrockModel"
```

---

## Task 4: Delete `inline_agent.py` + `bedrock.py`

**Files:**
- Delete: `src/channel/agents/inline_agent.py`
- Delete: `src/channel/agents/bedrock.py`
- Delete: `tests/unit/test_agents_inline_agent.py`
- Delete: `tests/unit/test_agents_bedrock.py`

These modules are superseded by Strands. Nothing in the codebase currently imports them (verified by grep). The CDK IAM grant for `bedrock:InvokeInlineAgent` stays — Strands' `BedrockModel` doesn't use it, but removing the grant is a separate cleanup that can ride in a later refactor.

- [ ] **Step 1: Verify no remaining importers**

```bash
grep -rn "inline_agent\|agents\.bedrock\b" src/ tests/ ui/ scripts/ 2>/dev/null | grep -v "^src/channel/agents/inline_agent.py\|^src/channel/agents/bedrock.py\|^tests/unit/test_agents_"
```

Expected: empty (no other files import these modules).

If anything else references them, fix the caller first.

- [ ] **Step 2: Delete the files**

```bash
git rm src/channel/agents/inline_agent.py src/channel/agents/bedrock.py tests/unit/test_agents_inline_agent.py tests/unit/test_agents_bedrock.py
```

- [ ] **Step 3: Verify the suite still passes**

```bash
uv run inv test-unit
```

Expected: 300 - (count of deleted tests) + (new chat_agent + strands_sse tests) pass. No collection errors from missing imports.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(channel-7b): delete inline_agent + bedrock (superseded by Strands)"
```

---

## Task 5: Wire Strands into `POST /messages` — rename `_stream_canned_reply` to `_stream_bedrock_reply`

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

This is the load-bearing task. The existing `_stream_canned_reply` async generator becomes `_stream_bedrock_reply` and gains:
1. `build_agent(model_id=payload.model)` construction.
2. `async for event in agent.stream_async(payload.message)` loop.
3. `translate_event(event)` per event; emit `sse_delta(text)` on `("delta", text)`; capture `stop_reason` + token counts on `("stop", ...)` / `("usage", ...)`; ignore `("skip", _)`.
4. The full assistant text is accumulated locally as `delta`s stream in, then `storage.put_message(text=accumulated_text, model=resolve_model_id(payload.model), input_tokens=, output_tokens=)` persists the assistant turn.
5. `storage.update_chat_index(...)` follows unchanged.
6. `sse_done(...)` with the captured metadata closes the stream.

The structure mirrors `_stream_canned_reply` exactly — only the inner content-generation loop swaps from word-by-word string slicing to Strands streaming. All existing tests for the user_persisted / chat-index / 404 paths keep passing.

- [ ] **Step 1: Write the failing test**

Replace the existing `test_post_message_returns_sse_with_canned_stream` test in `tests/unit/test_chats_api.py` (which currently asserts the canned word-by-word stream) with this real-Strands test:

```python
def test_post_message_returns_sse_via_strands(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat, Message, MessageRole

    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="claude-sonnet-4-6",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)

    persisted: list[Message] = []

    def fake_put(**kwargs: Any) -> Message:
        msg = Message(
            chat_id=kwargs["chat_id"],
            msg_id=f"m-{len(persisted)}",
            role=kwargs["role"],
            text=kwargs["text"],
            model=kwargs.get("model"),
            input_tokens=kwargs.get("input_tokens"),
            output_tokens=kwargs.get("output_tokens"),
            created_at="t",
        )
        persisted.append(msg)
        return msg

    monkeypatch.setattr("channel.api.chats.storage.put_message", fake_put)
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)

    # Fake Strands Agent that yields a deterministic event sequence.
    async def fake_stream(self, prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Hello"}}}}
        yield {"event": {"contentBlockDelta": {"delta": {"text": " world"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}
        yield {
            "event": {
                "metadata": {
                    "usage": {"inputTokens": 12, "outputTokens": 5},
                }
            }
        }

    class FakeAgent:
        stream_async = fake_stream

    monkeypatch.setattr(
        "channel.api.chats.build_agent", lambda **_: FakeAgent()
    )

    response = client.post(
        "/api/chats/c1/messages",
        json={"message": "hi", "model": "claude-sonnet-4-6", "effort": "medium"},
    )
    assert response.status_code == 200
    body = response.text

    assert "user_persisted" in body
    assert '"type": "delta"' in body
    assert "Hello" in body and "world" in body
    assert '"type": "done"' in body
    assert '"stop_reason": "end_turn"' in body
    assert '"input_tokens": 12' in body
    assert '"output_tokens": 5' in body

    assert [m.role for m in persisted] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert persisted[1].text == "Hello world"
    assert persisted[1].input_tokens == 12
    assert persisted[1].output_tokens == 5
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_chats_api.py::test_post_message_returns_sse_via_strands -v
```

Expected: failure — `build_agent` not imported / called from `chats.py` yet.

- [ ] **Step 3: Replace `_stream_canned_reply` with `_stream_bedrock_reply`**

In `src/channel/api/chats.py`:

a) Drop the `_CANNED_REPLY` and `_CANNED_MODEL` constants.

b) Replace the existing `_sse` helper and `_stream_canned_reply` with imports from the new translator + a Bedrock streamer:

```python
from channel.agents.chat_agent import build_agent, resolve_model_id
from channel.agents.strands_sse import (
    sse_delta,
    sse_done,
    sse_user_persisted,
    translate_event,
)


async def _stream_bedrock_reply(
    *,
    chat,
    user_message: str,
    model: str,
    claims: dict[str, Any],
):
    """Persist the user turn, stream Strands events, persist the assistant turn."""

    user_msg = storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.USER,
        text=user_message,
        model=None,
    )
    yield sse_user_persisted(msg_id=user_msg.msg_id, seq=0)

    agent = build_agent(model_id=model)
    accumulated = []
    stop_reason = "end_turn"
    input_tokens = 0
    output_tokens = 0

    async for event in agent.stream_async(user_message):
        kind, payload = translate_event(event)
        if kind == "delta":
            accumulated.append(payload)
            yield sse_delta(payload)
        elif kind == "stop":
            stop_reason = payload["stop_reason"]
        elif kind == "usage":
            input_tokens = payload["input_tokens"]
            output_tokens = payload["output_tokens"]

    assistant_text = "".join(accumulated)
    assistant_msg = storage.put_message(
        chat_id=chat.chat_id,
        role=MessageRole.ASSISTANT,
        text=assistant_text,
        model=resolve_model_id(model),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    storage.update_chat_index(
        user_id=claims["sub"],
        chat=chat,
        last_user_preview=user_message,
        delta_count=2,
        last_message_at=assistant_msg.created_at,
    )

    yield sse_done(
        msg_id=assistant_msg.msg_id,
        seq=1,
        model=resolve_model_id(model),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop_reason=stop_reason,
    )
```

c) Update the `post_message` handler to pass `payload.model` (with default `"claude-sonnet-4-6"`) into the streamer:

```python
@router.post("/{chat_id}/messages")
async def post_message(
    payload: SendMessageRequest,
    chat_id: str = Path(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> StreamingResponse:
    chat = await _load_owned_chat(chat_id, claims["sub"])
    model = payload.model or "claude-sonnet-4-6"

    replay: dict[str, Any] | None = None
    if idempotency_key:
        existing = storage.reserve_idempotency_key(
            user_id=claims["sub"], key=idempotency_key
        )
        if existing is not None and existing.get("result"):
            replay = existing["result"]

    if replay is not None:
        async def _replay():
            yield sse_user_persisted(
                msg_id=replay["user_msg_id"], seq=0
            )
            yield sse_delta(replay["text"])
            yield sse_done(**replay["done"])

        return StreamingResponse(_replay(), media_type="text/event-stream")

    async def _produce():
        # Capture msg_ids while streaming so the idempotency replay payload
        # can echo the real ids (7a's known limitation: was "n/a" before).
        produced_user_msg_id = None
        produced_done: dict[str, Any] | None = None
        async for chunk in _stream_bedrock_reply(
            chat=chat, user_message=payload.message, model=model, claims=claims
        ):
            # The first user_persisted byte-blob carries the real user msg_id;
            # the final `done` carries the assistant msg_id. We capture them by
            # peeking at the JSON before yielding the bytes verbatim.
            import json as _json
            try:
                event = _json.loads(chunk[6:-2])
            except Exception:
                event = None
            if event:
                if event.get("type") == "user_persisted" and produced_user_msg_id is None:
                    produced_user_msg_id = event["msg_id"]
                elif event.get("type") == "done":
                    produced_done = {
                        "msg_id": event["msg_id"],
                        "seq": event["seq"],
                        "model": event["model"],
                        "input_tokens": event["input_tokens"],
                        "output_tokens": event["output_tokens"],
                        "stop_reason": event["stop_reason"],
                    }
            yield chunk
        if idempotency_key and produced_user_msg_id and produced_done:
            storage.store_idempotency_result(
                user_id=claims["sub"],
                key=idempotency_key,
                payload={
                    "user_msg_id": produced_user_msg_id,
                    "text": "",  # populated by re-reading the message row at replay time;
                                 # for canned-stream era this was the full text. Switch back
                                 # to capturing accumulated text in 7c.
                    "done": produced_done,
                },
            )

    return StreamingResponse(_produce(), media_type="text/event-stream")
```

> **Idempotency-payload caveat:** the new payload captures real `msg_id`s (fixes 7a's `"n/a"` limitation) but the `"text"` field is left empty in the snippet above. The replay generator currently emits one `delta` with `replay["text"]` — if that's empty, the UI would replay an empty assistant turn. **Resolution path:** accumulate the assistant text inside `_stream_bedrock_reply` and surface it via the same JSON-peek mechanism (add a sentinel SSE event with `type=accumulated` that the `_produce` wrapper consumes and strips, OR refactor `_stream_bedrock_reply` to return both the byte stream AND the accumulated text via a context object). **Task 6 below handles this cleanly** — for Task 5, just leave the text field empty and accept that the replay path's text content is broken until Task 6 lands. The 3 existing idempotency tests (replay shortcut, fresh-key store, replay generator emits the stored text) all need updating; do that in Task 6.

- [ ] **Step 4: Run all chats_api tests**

```bash
uv run pytest tests/unit/test_chats_api.py -v
```

Expected: the new `test_post_message_returns_sse_via_strands` passes; the existing user-persisted, 404, validation tests keep passing. The two idempotency tests (`test_post_message_replays_on_duplicate_idempotency_key`, `test_post_message_stores_result_on_fresh_idempotency_key`) FAIL — `replay["text"]` is now empty. Mark these as **expected failures until Task 6**:

```python
@pytest.mark.xfail(reason="text capture lands in Task 6")
def test_post_message_replays_on_duplicate_idempotency_key(...):
    ...
```

- [ ] **Step 5: Coverage check**

```bash
uv run pytest tests/unit/test_chats_api.py --cov=channel.api.chats --cov-report=term-missing
```

Expected: 100% on `channel.api.chats` (the xfail tests still execute the relevant lines).

- [ ] **Step 6: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "feat(channel-7b): wire Strands streaming into POST /messages"
```

---

## Task 6: Capture accumulated assistant text into idempotency payload

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

Resolve Task 5's xfail tests by accumulating the assistant text inside `_stream_bedrock_reply` and surfacing it to the `_produce` wrapper.

The cleanest factoring: refactor `_stream_bedrock_reply` to take a `state: dict[str, Any]` parameter that it populates as it streams. The `_produce` wrapper inspects `state` after the stream completes and uses `state["assistant_text"]` for the idempotency store.

- [ ] **Step 1: Update `_stream_bedrock_reply` to populate state**

Add a `state` parameter (or use a closure-captured dict in the `_produce` body) and write the accumulated text + user msg id + done event into it:

```python
async def _stream_bedrock_reply(
    *,
    chat,
    user_message: str,
    model: str,
    claims: dict[str, Any],
    state: dict[str, Any] | None = None,
):
    state = state if state is not None else {}
    user_msg = storage.put_message(...)
    state["user_msg_id"] = user_msg.msg_id
    yield sse_user_persisted(msg_id=user_msg.msg_id, seq=0)

    # ... (existing Strands streaming loop, accumulating into `accumulated`)

    assistant_text = "".join(accumulated)
    state["assistant_text"] = assistant_text
    state["input_tokens"] = input_tokens
    state["output_tokens"] = output_tokens
    state["stop_reason"] = stop_reason

    assistant_msg = storage.put_message(..., text=assistant_text, ...)
    state["assistant_msg_id"] = assistant_msg.msg_id

    storage.update_chat_index(...)

    yield sse_done(...)
```

- [ ] **Step 2: Update `_produce` to use state**

Replace the JSON-peek hack from Task 5 with direct state inspection:

```python
async def _produce():
    state: dict[str, Any] = {}
    async for chunk in _stream_bedrock_reply(
        chat=chat,
        user_message=payload.message,
        model=model,
        claims=claims,
        state=state,
    ):
        yield chunk
    if idempotency_key and state.get("assistant_msg_id"):
        storage.store_idempotency_result(
            user_id=claims["sub"],
            key=idempotency_key,
            payload={
                "user_msg_id": state["user_msg_id"],
                "text": state["assistant_text"],
                "done": {
                    "msg_id": state["assistant_msg_id"],
                    "seq": 1,
                    "model": resolve_model_id(model),
                    "input_tokens": state["input_tokens"],
                    "output_tokens": state["output_tokens"],
                    "stop_reason": state["stop_reason"],
                },
            },
        )
```

Drop the `import json as _json` and the JSON-peek block from Task 5.

- [ ] **Step 3: Remove `@pytest.mark.xfail` from the two idempotency tests**

In `tests/unit/test_chats_api.py`, remove the xfail decorators added in Task 5.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_chats_api.py -v
```

Expected: all 16+ tests pass cleanly.

- [ ] **Step 5: Coverage check**

```bash
uv run pytest tests/unit/test_chats_api.py --cov=channel.api.chats --cov-report=term-missing
```

Expected: 100%.

- [ ] **Step 6: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "fix(channel-7b): capture real assistant text into idempotency payload"
```

---

## Task 7: CDK — raise Lambda timeout + extend Bedrock IAM for Opus + fix stale Mangum comment

**Files:**
- Modify: `infra/stacks/channel_stack.py`

- [ ] **Step 1: Raise the Lambda timeout**

Edit `infra/stacks/channel_stack.py` line 384. Change `timeout=cdk.Duration.seconds(30),` to `timeout=cdk.Duration.minutes(5),`. Add a brief comment above explaining why:

```python
# 5 min for streaming chats — Bedrock responses on long prompts can
# exceed 30s. AWSLWA streams to the Function URL as bytes arrive, so
# the long handler runtime doesn't add user-perceived latency.
timeout=cdk.Duration.minutes(5),
```

- [ ] **Step 2: Extend Bedrock IAM for Opus**

Find the `bedrock:InvokeModel` policy block at lines 339-347. Add the Opus ARN to the `resources` list. The plan uses the placeholder `anthropic.claude-opus-4-7`; **before committing, verify the actual current Opus model ID** via:

```bash
aws bedrock list-foundation-models --query "modelSummaries[?providerName=='Anthropic'].modelId" --output table
```

Pick the latest Opus entry from that output. Then update:

```python
api_role.add_to_policy(
    iam.PolicyStatement(
        actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources=[
            f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-sonnet-4-6",
            f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
            f"arn:aws:bedrock:{self.region}::foundation-model/<actual-opus-id-from-aws-cli>",
        ],
    )
)
```

Also update `_MODEL_ID_MAP` in `src/channel/agents/chat_agent.py` to match the actual ARN.

- [ ] **Step 3: Fix the stale `FastAPI + Mangum` comment**

Find the module-level docstring at line 7:

```python
#   - Lambda function for the API (FastAPI + Mangum)
```

Change to:

```python
#   - Lambda function for the API (FastAPI + uvicorn behind AWSLWA)
```

- [ ] **Step 4: Synth + lint**

```bash
uv run inv synth 2>&1 | tail -20
uv run ruff check infra/stacks/channel_stack.py
uv run mypy infra/stacks/channel_stack.py
```

Expected: synth succeeds (may fail in a worktree per the known Docker-bundling issue; in that case verify via `aws_cdk.assertions.Template.from_stack` directly). ruff + mypy clean.

- [ ] **Step 5: Commit**

```bash
git add infra/stacks/channel_stack.py
git commit -m "feat(channel-7b): raise Lambda timeout to 5min, extend Bedrock IAM for Opus"
```

---

## Task 8: `/api/models` endpoint

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

Add `GET /api/models` that returns the server allowlist of model IDs the picker can use. The list is the keys of `_MODEL_ID_MAP` from `chat_agent.py` plus display metadata that lives server-side.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_chats_api.py`:

```python
def test_list_models_returns_allowlist(client: TestClient) -> None:
    response = client.get("/api/models")
    assert response.status_code == 200
    body = response.json()
    assert "models" in body
    ids = {m["id"] for m in body["models"]}
    assert ids == {"claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-7"}
    sonnet = next(m for m in body["models"] if m["id"] == "claude-sonnet-4-6")
    assert sonnet["family"] == "anthropic"
    assert sonnet["supports_streaming"] is True
```

- [ ] **Step 2: Add the endpoint to `chats.py`**

Add a constant + handler in `src/channel/api/chats.py`. Place near the top of the router (after the existing imports):

```python
_MODEL_DISPLAY = [
    {"id": "claude-opus-4-7",   "label": "Claude Opus 4.7",   "family": "anthropic", "tier": "Flagship", "supports_streaming": True},
    {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6", "family": "anthropic", "tier": "Balanced", "supports_streaming": True},
    {"id": "claude-haiku-4-5",  "label": "Claude Haiku 4.5",  "family": "anthropic", "tier": "Fast",     "supports_streaming": True},
]


@router.get("/models", include_in_schema=True)
async def list_models(
    _claims: dict[str, Any] = Depends(require_mgmt_user),
) -> dict[str, Any]:
    """Server allowlist of model IDs the picker can choose from."""

    return {"models": _MODEL_DISPLAY}
```

> **Route prefix note:** the router already declares `prefix="/chats"`, so `@router.get("/models")` would mount at `/api/chats/models`. Either (a) move this endpoint to a separate router with `prefix=""` mounted at `/api`, or (b) accept `/api/chats/models` as the path. **Recommended: (a)** — create a tiny new router in `src/channel/api/models.py` so the path is `/api/models` per the spec. Update Task 8's implementation to put the constant + handler in that new file and mount it from `src/channel/api/main.py`.

- [ ] **Step 3: Run tests + coverage**

```bash
uv run pytest tests/unit/test_chats_api.py -v --cov=channel.api -- term-missing
```

Expected: 100%.

- [ ] **Step 4: Commit**

```bash
git add src/channel/api/chats.py src/channel/api/models.py src/channel/api/main.py tests/unit/test_chats_api.py
git commit -m "feat(channel-7b): GET /api/models — server-side model allowlist"
```

---

## Task 9: `/regenerate` endpoint

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `src/channel/storage.py` (add a `delete_last_assistant_message` helper)
- Modify: `tests/unit/test_chats_api.py`
- Modify: `tests/unit/test_storage.py`

The regenerate endpoint:
1. Looks up + own-checks the chat (404 on mismatch).
2. Deletes the last assistant message row (DDB).
3. Re-streams the assistant turn from the last user message (which stays put).
4. Optionally accepts `{model, effort}` overrides — if absent, uses `chat.model_default`.

Body shape: `{model?: str, effort?: str}` — both optional.

- [ ] **Step 1: Add `delete_last_assistant_message` to `storage.py`**

```python
def delete_last_assistant_message(chat_id: str) -> Message | None:
    """Drop the most recent assistant message in a chat.

    Returns the deleted message, or None if the chat had no assistant
    messages.  Used by ``POST /api/chats/{id}/regenerate``.
    """

    msgs, _ = list_messages(chat_id, limit=50, cursor=None)
    for msg in reversed(msgs):
        if msg.role == MessageRole.ASSISTANT:
            _get_table().delete_item(
                Key={
                    "PK": f"CHAT#{chat_id}",
                    "SK": f"MSG#{msg.created_at}#{msg.msg_id}",
                }
            )
            return msg
    return None
```

Add a unit test:

```python
def test_delete_last_assistant_message_drops_only_the_assistant_row(
    table: FakeTable,
) -> None:
    chat = st.create_chat(user_id="u", title=None, model_default="m")
    st.put_message(chat_id=chat.chat_id, role=MessageRole.USER, text="hi", model=None)
    st.put_message(
        chat_id=chat.chat_id, role=MessageRole.ASSISTANT, text="hello", model="m"
    )
    deleted = st.delete_last_assistant_message(chat.chat_id)
    assert deleted is not None and deleted.text == "hello"

    msgs, _ = st.list_messages(chat.chat_id, limit=10, cursor=None)
    assert [m.role for m in msgs] == [MessageRole.USER]


def test_delete_last_assistant_message_returns_none_when_no_assistant_rows(
    table: FakeTable,
) -> None:
    chat = st.create_chat(user_id="u", title=None, model_default="m")
    st.put_message(chat_id=chat.chat_id, role=MessageRole.USER, text="hi", model=None)
    assert st.delete_last_assistant_message(chat.chat_id) is None
```

Update `FakeTable` to support `delete_item`:

```python
def delete_item(self, Key: dict[str, str]) -> dict[str, Any]:
    self.items.pop((Key["PK"], Key["SK"]), None)
    return {}
```

- [ ] **Step 2: Add the `/regenerate` handler to `chats.py`**

```python
class RegenerateRequest(BaseModel):
    model: str | None = None
    effort: str | None = None


@router.post("/{chat_id}/regenerate")
async def regenerate(
    payload: RegenerateRequest,
    chat_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> StreamingResponse:
    chat = await _load_owned_chat(chat_id, claims["sub"])
    model = payload.model or chat.model_default or "claude-sonnet-4-6"

    storage.delete_last_assistant_message(chat_id)

    # Reuse the same Strands stream path; the trigger is the user's
    # last message, which we re-read from storage.
    msgs, _ = storage.list_messages(chat_id, limit=50, cursor=None)
    last_user = next(
        (m for m in reversed(msgs) if m.role == MessageRole.USER), None
    )
    if last_user is None:
        raise HTTPException(
            status_code=400, detail="Cannot regenerate — chat has no user messages."
        )

    return StreamingResponse(
        _stream_bedrock_reply(
            chat=chat,
            user_message=last_user.text,
            model=model,
            claims=claims,
        ),
        media_type="text/event-stream",
    )
```

Add the `RegenerateRequest` model to `src/channel/models.py` (keep models in one place):

```python
class RegenerateRequest(BaseModel):
    """Request body for POST /api/chats/{id}/regenerate."""

    model: str | None = None
    effort: str | None = None
```

- [ ] **Step 3: Write tests**

```python
def test_regenerate_drops_last_assistant_and_restreams(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat, Message, MessageRole

    chat = Chat(
        chat_id="c1",
        user_id="u-1",
        title="t",
        created_at="t",
        last_message_at="t",
        model_default="claude-sonnet-4-6",
    )
    monkeypatch.setattr("channel.api.chats.storage.get_chat_by_id", lambda _: chat)

    deleted_calls = []
    monkeypatch.setattr(
        "channel.api.chats.storage.delete_last_assistant_message",
        lambda chat_id: deleted_calls.append(chat_id),
    )
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages",
        lambda *_a, **_kw: (
            [
                Message(
                    chat_id="c1",
                    msg_id="u-1",
                    role=MessageRole.USER,
                    text="redo",
                    created_at="t",
                )
            ],
            None,
        ),
    )

    async def fake_stream(self, prompt):
        assert prompt == "redo"
        yield {"event": {"contentBlockDelta": {"delta": {"text": "again"}}}}
        yield {"event": {"messageStop": {"stopReason": "end_turn"}}}

    class FakeAgent:
        stream_async = fake_stream

    monkeypatch.setattr("channel.api.chats.build_agent", lambda **_: FakeAgent())
    monkeypatch.setattr(
        "channel.api.chats.storage.put_message",
        lambda **kw: Message(
            chat_id=kw["chat_id"],
            msg_id="m-new",
            role=kw["role"],
            text=kw["text"],
            created_at="t",
        ),
    )
    monkeypatch.setattr("channel.api.chats.storage.update_chat_index", lambda **_: None)

    response = client.post("/api/chats/c1/regenerate", json={})
    assert response.status_code == 200
    assert "again" in response.text
    assert deleted_calls == ["c1"]


def test_regenerate_returns_400_when_no_user_messages(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from channel.models import Chat

    monkeypatch.setattr(
        "channel.api.chats.storage.get_chat_by_id",
        lambda _: Chat(
            chat_id="c1",
            user_id="u-1",
            title="t",
            created_at="t",
            last_message_at="t",
            model_default="m",
        ),
    )
    monkeypatch.setattr(
        "channel.api.chats.storage.delete_last_assistant_message", lambda _: None
    )
    monkeypatch.setattr(
        "channel.api.chats.storage.list_messages", lambda *_a, **_kw: ([], None)
    )

    response = client.post("/api/chats/c1/regenerate", json={})
    assert response.status_code == 400
```

- [ ] **Step 4: Run tests + coverage**

```bash
uv run pytest tests/unit/test_chats_api.py tests/unit/test_storage.py -v --cov=channel.api.chats --cov=channel.storage --cov-report=term-missing
```

Expected: 100% on both modules.

- [ ] **Step 5: Commit**

```bash
git add src/channel/api/chats.py src/channel/storage.py src/channel/models.py tests/unit/test_chats_api.py tests/unit/test_storage.py
git commit -m "feat(channel-7b): POST /api/chats/{id}/regenerate"
```

---

## Task 10: UI — `listModels()` + `regenerate()` api wrappers

**Files:**
- Modify: `ui/src/api.js`
- Modify: `ui/src/api.test.js`

Add two new named-export wrappers matching the conventions from 7a.

- [ ] **Step 1: Add wrappers**

```js
export async function listModels() {
  const response = await fetch(`${BASE}/api/models`, { headers: authHeader() });
  if (!response.ok) throw new Error(`listModels ${response.status}`);
  return response.json();
}

export async function regenerate(chatId, { model, effort, signal } = {}) {
  const response = await fetch(`${BASE}/api/chats/${chatId}/regenerate`, {
    method: "POST",
    headers: { ...authHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({ model, effort }),
    signal,
  });
  if (!response.ok) throw new Error(`regenerate ${response.status}`);
  return response;
}
```

- [ ] **Step 2: Add tests**

```js
describe("listModels", () => {
  it("GETs /api/models and returns parsed body", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ models: [{ id: "claude-sonnet-4-6" }] }),
    });
    const result = await listModels();
    expect(result.models[0].id).toBe("claude-sonnet-4-6");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/models");
  });

  it("throws on non-ok", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500 });
    await expect(listModels()).rejects.toThrow(/listModels 500/);
  });
});

describe("regenerate", () => {
  it("POSTs to /regenerate and returns the raw Response", async () => {
    const fakeResponse = { ok: true, status: 200, body: "stream" };
    fetchMock.mockResolvedValue(fakeResponse);
    const result = await regenerate("c1", { model: "claude-opus-4-7" });
    expect(result).toBe(fakeResponse);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/chats/c1/regenerate");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      model: "claude-opus-4-7",
      effort: undefined,
    });
  });

  it("forwards abort signal", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200 });
    const controller = new AbortController();
    await regenerate("c1", { signal: controller.signal });
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it("throws on non-ok", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500 });
    await expect(regenerate("c1", {})).rejects.toThrow(/regenerate 500/);
  });
});
```

- [ ] **Step 3: Run + coverage**

```bash
cd ui && npx vitest run src/api.test.js --coverage
```

Expected: 100%.

- [ ] **Step 4: Commit**

```bash
git add ui/src/api.js ui/src/api.test.js
git commit -m "feat(channel-7b): API wrappers for listModels + regenerate"
```

---

## Task 11: UI — ModelPicker reads from `/api/models`

**Files:**
- Modify: `ui/src/app/ModelPicker.jsx`
- Modify: `ui/src/app/ModelPicker.test.jsx`
- Modify: `ui/src/app/data.js`

Today `ModelPicker` reads the hardcoded `MODELS` array from `data.js`. Phase 7b sources the valid ids from `/api/models` and merges with client-side display metadata. If the API call fails (offline / network blip), fall back to the static `MODELS` so the picker never goes blank.

- [ ] **Step 1: Read existing ModelPicker**

```bash
grep -n "MODELS\|useEffect\|import" ui/src/app/ModelPicker.jsx | head -10
```

- [ ] **Step 2: Wire the API call**

Add a `useEffect` that calls `listModels()` on mount; merge the server allowlist with `MODELS` display metadata by `id`:

```jsx
import { useEffect, useState } from "react";
import { listModels } from "../api.js";
import { MODELS } from "./data.js";

function mergeModels(serverModels) {
  return serverModels.map((sm) => {
    const display = MODELS.find((m) => m.id === sm.id) || {};
    return { ...display, ...sm };
  });
}

export default function ModelPicker({ value, onChange, open, onClose }) {
  const [models, setModels] = useState(MODELS);  // fallback on first render
  useEffect(() => {
    listModels()
      .then(({ models: server }) => setModels(mergeModels(server)))
      .catch(() => { /* keep fallback */ });
  }, []);
  // ... existing render using `models` instead of `MODELS`
}
```

- [ ] **Step 3: Update the test**

`ModelPicker.test.jsx`:

```jsx
vi.mock("../api.js", () => ({
  listModels: vi.fn(),
}));
import * as api from "../api.js";

beforeEach(() => vi.clearAllMocks());

it("falls back to static MODELS on API failure", async () => {
  api.listModels.mockRejectedValue(new Error("network"));
  render(<ModelPicker value={null} onChange={() => {}} open onClose={() => {}} />);
  // The fallback should display every static model id.
  await waitFor(() => {
    expect(screen.getByText(/Sonnet 4\.6/i)).toBeInTheDocument();
  });
});

it("renders server allowlist on success", async () => {
  api.listModels.mockResolvedValue({
    models: [{ id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" }],
  });
  render(<ModelPicker value={null} onChange={() => {}} open onClose={() => {}} />);
  await waitFor(() => {
    expect(screen.getByText(/Sonnet 4\.6/i)).toBeInTheDocument();
  });
  // Models NOT in the server allowlist must NOT render.
  expect(screen.queryByText(/Opus 4\.8/i)).toBeNull();
});
```

- [ ] **Step 4: Run + coverage**

```bash
cd ui && npx vitest run src/app/ModelPicker.test.jsx --coverage
```

Expected: 100% on ModelPicker.jsx including both branches of the API result.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/ModelPicker.jsx ui/src/app/ModelPicker.test.jsx
git commit -m "feat(channel-7b): ModelPicker reads allowlist from /api/models"
```

---

## Task 12: UI — `useChatStream.regenerate()`

**Files:**
- Modify: `ui/src/hooks/useChatStream.js`
- Modify: `ui/src/hooks/useChatStream.test.js`

Add a `regenerate({model, effort})` method to the hook. Behavior:
1. Drop the last assistant turn from local state (`turns[turns.length-1]` if role === "assistant").
2. POST to `/api/chats/{id}/regenerate` via the new `api.regenerate` wrapper.
3. Parse the SSE stream identically to `send()` — same `user_persisted` / `delta` / `done` shapes.

Most of the streaming logic is identical to `send()`; factor the shared SSE-reader loop into a private helper to avoid duplication.

- [ ] **Step 1: Write the failing tests**

```js
it("regenerate drops last assistant turn and re-streams", async () => {
  api.getChat.mockResolvedValue({
    chat: { chat_id: "c1" },
    messages: [
      { msg_id: "u1", role: "user", text: "hi" },
      { msg_id: "a1", role: "assistant", text: "first reply" },
    ],
    next_cursor: null,
  });
  api.regenerate.mockResolvedValue({
    ok: true,
    body: makeMockResponseBody([
      { type: "user_persisted", msg_id: "u1", seq: 0 },
      { type: "delta", text: "second" },
      {
        type: "done",
        msg_id: "a2",
        seq: 1,
        model: "anthropic.claude-sonnet-4-6",
        input_tokens: 0,
        output_tokens: 0,
        stop_reason: "end_turn",
      },
    ]),
  });

  const { result } = renderHook(() => useChatStream("c1"));
  await waitFor(() => expect(result.current.status).toBe("idle"));

  await act(async () => {
    await result.current.regenerate({ model: "claude-opus-4-7" });
  });

  expect(result.current.turns).toHaveLength(2);
  expect(result.current.turns[1].text).toBe("second");
  expect(api.regenerate).toHaveBeenCalledWith(
    "c1",
    expect.objectContaining({ model: "claude-opus-4-7" }),
  );
});
```

- [ ] **Step 2: Implement `regenerate` in `useChatStream.js`**

Extract a `_readSseStream(response, tempAsstId)` helper that contains the existing SSE-reader loop from `send`. Then implement `regenerate`:

```js
const regenerate = useCallback(
  async ({ model, effort } = {}) => {
    if (!chatId) return;
    setError(null);

    // Drop the last assistant turn locally; re-add empty streaming row.
    const tempAsstId = `tmp-a-${crypto.randomUUID()}`;
    setTurns((prev) => {
      const next = prev[prev.length - 1]?.role === "assistant"
        ? prev.slice(0, -1)
        : [...prev];
      return [...next, { msg_id: tempAsstId, role: "assistant", text: "", streaming: true }];
    });
    setStatus("streaming");

    const controller = new AbortController();
    abortRef.current = controller;
    let response;
    try {
      response = await api.regenerate(chatId, { model, effort, signal: controller.signal });
    } catch (err) {
      setError(err);
      setStatus("error");
      return;
    }
    await _readSseStream(response, tempAsstId);
  },
  [chatId],
);
```

`_readSseStream` is the existing while-loop refactored into a private function. Return `{turns, send, regenerate, abort, status, error}` from the hook.

- [ ] **Step 3: Run + coverage**

```bash
cd ui && npx vitest run src/hooks/useChatStream.test.js --coverage
```

Expected: 100% on `useChatStream.js`. Add tests as needed for the regenerate error path + no-assistant-to-drop branch.

- [ ] **Step 4: Commit**

```bash
git add ui/src/hooks/useChatStream.js ui/src/hooks/useChatStream.test.js
git commit -m "feat(channel-7b): useChatStream.regenerate() — drop last assistant + re-stream"
```

---

## Task 13: UI — Conversation regenerate button + model-label fix

**Files:**
- Modify: `ui/src/app/Conversation.jsx`
- Modify: `ui/src/app/Conversation.test.jsx`

Two changes:
1. **Add a regenerate button** to the last assistant turn (visible when not streaming). Calls `regenerate({})` with no overrides (so it uses the chat's default model).
2. **Fix the 7a model-label regression** — assistant header should render a friendly label (`"Claude Sonnet 4.6 · Medium"`), not the raw model ARN (`"anthropic.claude-sonnet-4-6"`). Map the ARN to a label via the same `MODELS`-driven lookup the picker uses.

- [ ] **Step 1: Implement the label helper**

Add to `ui/src/app/Conversation.jsx`:

```jsx
import { MODELS } from "./data.js";

function modelLabel(raw) {
  if (!raw) return "";
  // Bedrock ARNs come back as anthropic.claude-sonnet-4-6 → strip prefix.
  const shortId = raw.replace(/^anthropic\./, "");
  const display = MODELS.find((m) => m.id === shortId);
  return display ? display.name : raw;
}
```

Use it when rendering the assistant header:

```jsx
<div className="msg-meta">{modelLabel(turn.model)}</div>
```

- [ ] **Step 2: Add the regenerate button**

Find the existing assistant-turn render block. Add a button visible when `turn.role === "assistant"` and `!turn.streaming`:

```jsx
{turn.role === "assistant" && !turn.streaming && (
  <button
    type="button"
    className="msg-action"
    onClick={() => regenerate({})}
  >
    Regenerate
  </button>
)}
```

Wire `regenerate` from `useChatStream` (which now returns it):

```jsx
const { turns, send, regenerate, abort, status } = useChatStream(chatId);
```

- [ ] **Step 3: Tests**

```jsx
it("renders friendly model label, not raw ARN", () => {
  useChatStreamModule.useChatStream.mockReturnValue({
    turns: [
      { role: "user", text: "hi", msg_id: "u1" },
      { role: "assistant", text: "ok", msg_id: "a1", model: "anthropic.claude-sonnet-4-6" },
    ],
    send: vi.fn(),
    regenerate: vi.fn(),
    abort: vi.fn(),
    status: "idle",
  });
  render(...);
  expect(screen.getByText("Claude Sonnet 4.6")).toBeInTheDocument();
  expect(screen.queryByText(/anthropic\./)).toBeNull();
});

it("regenerate button calls hook.regenerate", () => {
  const regenerate = vi.fn();
  useChatStreamModule.useChatStream.mockReturnValue({
    turns: [
      { role: "user", text: "hi", msg_id: "u1" },
      { role: "assistant", text: "ok", msg_id: "a1", streaming: false },
    ],
    send: vi.fn(),
    regenerate,
    abort: vi.fn(),
    status: "idle",
  });
  render(...);
  fireEvent.click(screen.getByText("Regenerate"));
  expect(regenerate).toHaveBeenCalled();
});

it("regenerate button is hidden while streaming", () => {
  useChatStreamModule.useChatStream.mockReturnValue({
    turns: [
      { role: "user", text: "hi", msg_id: "u1" },
      { role: "assistant", text: "", msg_id: "a1", streaming: true },
    ],
    send: vi.fn(),
    regenerate: vi.fn(),
    abort: vi.fn(),
    status: "streaming",
  });
  render(...);
  expect(screen.queryByText("Regenerate")).toBeNull();
});
```

- [ ] **Step 4: Run + coverage**

```bash
cd ui && npx vitest run src/app/Conversation.test.jsx --coverage
```

Expected: 100% on `Conversation.jsx` including all three branches above.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/Conversation.jsx ui/src/app/Conversation.test.jsx
git commit -m "feat(channel-7b): Conversation regenerate button + friendly model label"
```

---

## Task 14: Verify with a real Bedrock model

**Files:** none modified.

Before the Strands AgentCore Memory spike (Task 15), confirm Strands invocation actually works against real Bedrock end-to-end. This is the smoke equivalent of Phase 7a's manual run.

- [ ] **Step 1: Start the stack**

```bash
uv run inv dev --seed
```

- [ ] **Step 2: Send a real request**

In a second terminal:

```bash
TOKEN=$(curl -s "http://localhost:8001/auth/login?test_email=smoke@example.com" -L | grep starter_mgmt_token | cut -d"'" -f4)
CHAT=$(curl -s -X POST "http://localhost:8001/api/chats" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{}' | jq -r .chat_id)
curl -N -X POST "http://localhost:8001/api/chats/$CHAT/messages" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message":"Say hello in 5 words."}'
```

Expected: real Bedrock SSE stream — `user_persisted`, several `delta`s with real Claude text, one `done` with non-zero token counts.

If this fails with permissions / "model not authorised" / etc., the IAM grant from Task 7 needs widening (or the model id needs verification against `aws bedrock list-foundation-models`).

If this succeeds: proceed.

- [ ] **Step 2: No commit — this is a smoke gate, not a code change.**

---

## Task 15: Strands AgentCore Memory adapter spike (informational, for 7c)

**Files:**
- Create: `docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md`

Per the spec's Open Questions section, three things about Strands' `AgentCoreMemory` adapter need verification before 7c can commit to either the "Strands auto-injection" path or the "manual recall escape hatch" path. **This task writes a short spike report** — no production code lands.

The three spike questions:

1. **Visibility:** can we log / inspect what the adapter injected into the model context per turn? (Drives debugging tooling for memory in prod.)
2. **Gating:** does the adapter let us threshold semantic recall by relevance score, or does it always inject top-K? (If only top-K, irrelevant memories pollute every turn.)
3. **Swallow-vs-throw:** if `create_event` fails (AgentCore unavailable), does the adapter raise, or can we configure it to log + swallow? (7c needs swallow semantics to preserve the asymmetric "DB loud, memory quiet" pattern from §5.)

- [ ] **Step 1: Read the Strands source for `AgentCoreMemory`**

```bash
uv run python -c "import inspect, strands.memory; print(inspect.getsourcefile(strands.memory))"
```

Expected: a path under `.venv/.../site-packages/strands/`. Open the file. Find the `AgentCoreMemory` (or equivalent) class.

If the adapter doesn't exist in Strands yet (it may be experimental in the version Task 1 pinned), the spike short-circuits: document "Adapter not present in Strands vX.Y — 7c will use manual `boto3.client('bedrock-agentcore').create_event` + `retrieve_memories` directly." That's still a valid spike outcome.

- [ ] **Step 2: Answer each spike question**

Read the adapter source and note:

(a) **Visibility:** look for any logger / hook / callback before the model invocation that exposes the recall payload. If absent, the answer is "no hooks — we'd need to subclass to add a `pre_inject` callback."

(b) **Gating:** look for a `score_threshold` / `min_score` / similar parameter. If absent, the answer is "top-K only — manual filtering required."

(c) **Swallow-vs-throw:** look at the adapter's `__aenter__` / `record_event` / `before_model_invocation` (or similar). If it raises bare exceptions, swallow needs subclassing. If it has a `failure_mode` / `on_error` knob, document it.

- [ ] **Step 3: Write the spike report**

Create `docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md` with:

```markdown
# Strands AgentCore Memory adapter spike — 7b plan-time

**Date:** 2026-05-31
**Status:** Informational
**Parent spec:** `docs/superpowers/specs/2026-05-30-bedrock-agentcore-chat-design.md`

## Strands version inspected

`strands-agents==<actual version from uv.lock>`

## Summary

<one-paragraph synthesis of the three answers below>

## Question 1 — Visibility

<finding + code reference>

## Question 2 — Gating

<finding + code reference>

## Question 3 — Swallow-vs-throw

<finding + code reference>

## Implication for 7c

Pick one:

- **Adopt Strands native recall** — visibility / gating / swallow all
  acceptable as shipped.
- **Adopt manual escape hatch** — set Strands adapter to write-only (or
  bypass entirely) and inject recall ourselves via system prompt.

7c will write the actual integration based on this decision.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md
git commit -m "docs(channel-7b): Strands AgentCore Memory adapter spike report"
```

---

## Task 16: CHANGELOG entry + CLAUDE.md sync

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: CHANGELOG `[Unreleased]` entry**

Edit `CHANGELOG.md`. Under the `[Unreleased]` section, add:

```markdown
### Added

- Real Bedrock streaming via Strands Agents — replaces the canned reply
  that 7a shipped. Three models served: Sonnet 4.6, Haiku 4.5, Opus 4.7.
- `GET /api/models` — server-side allowlist drives the ModelPicker.
- `POST /api/chats/{id}/regenerate` — drop the last assistant turn and
  re-stream from the same user message. New "Regenerate" button on the
  last assistant turn.

### Changed

- Lambda timeout raised from 30s to 5 min for streaming chats.
- Bedrock IAM extended to allowlist Opus 4.7.
- Assistant turn header renders the friendly model label (e.g. "Claude
  Sonnet 4.6") instead of the raw Bedrock ARN — fixes a 7a regression.

### Removed

- `src/channel/agents/inline_agent.py` and `src/channel/agents/bedrock.py`
  — superseded by Strands.
- Stale `(FastAPI + Mangum)` comment in the CDK stack docstring (the
  project has been on AWSLWA since pre-7a).
```

- [ ] **Step 2: CLAUDE.md updates**

In the `## Structure` block, replace the agents section:

```diff
-│       ├── agents/
-│       │   ├── __init__.py
-│       │   ├── bedrock.py     # Converse + converse_stream (raw Bedrock)
-│       │   └── inline_agent.py # invoke + invoke_stream (Bedrock inline agent)
+│       ├── agents/
+│       │   ├── __init__.py
+│       │   ├── chat_agent.py   # Strands Agent factory (build_agent / resolve_model_id)
+│       │   └── strands_sse.py  # Strands event → SSE byte translator
```

In the `## Structure` block, under the `api/` section, add `models.py` if Task 8 placed the `/api/models` endpoint there:

```diff
 │       └── api/
 │           ├── main.py        # FastAPI app + routes
+│           ├── models.py      # GET /api/models — server allowlist
 │           └── csp.py         # CSP violation reporting endpoint
```

- [ ] **Step 3: Verify lint**

```bash
uv run inv lint-backend
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs(channel-7b): CHANGELOG + CLAUDE.md sync for Strands + regenerate"
```

---

## Task 17: Pre-push + integration + smoke

**Files:** none modified.

- [ ] **Step 1: Pre-push**

```bash
uv run inv pre-push
```

Expected: lint + typecheck + unit + frontend all green.

- [ ] **Step 2: Integration**

```bash
docker ps | grep dynamodb-local || docker run -d -p 8000:8000 amazon/dynamodb-local:latest
uv run inv test-integration
```

Expected: 17+ tests green.

- [ ] **Step 3: Synth**

```bash
uv run inv synth
```

Expected: CloudFormation includes the new timeout (300s) and the Opus ARN in the IAM grant. (May fail locally in a worktree per the known Docker-bundling issue; verify via `aws_cdk.assertions.Template.from_stack` if so.)

- [ ] **Step 4: Manual smoke against `inv dev`**

```bash
uv run inv dev --seed
```

In another terminal, sign in via the test_email bypass and verify in the SPA:
- ModelPicker shows three options sourced from `/api/models`.
- Sending a message streams a real Bedrock reply (not the canned text).
- Reload restores the conversation.
- Regenerate button on the last assistant turn drops + re-streams.
- The assistant header reads "Claude Sonnet 4.6" (not "anthropic.claude-sonnet-4-6").

If anything fails, fix it (TDD: failing test first), re-run the gate.

- [ ] **Step 5: Commit (if any fixes)**

---

## Task 18: Open the PR

**Files:** none modified.

- [ ] **Step 1: Rebase + push**

Follow the W1–W7 push discipline from `.claude/agents/issue-worker.md`:

```bash
git fetch origin
git rebase origin/development
git log --oneline origin/development..HEAD
git push -u origin worktree-feat+channel-mvp-phase-7b:feat/channel-mvp-phase-7b
```

- [ ] **Step 2: Create a tracking issue (so the PR can close it)**

```bash
gh issue create --title "Phase 7b — Strands + real Bedrock streaming" \
  --label "size:l,priority:p1,status:ready" --body "$(cat <<'EOF'
Tracks Phase 7b per
`docs/superpowers/specs/2026-05-30-bedrock-agentcore-chat-design.md`
and `docs/superpowers/plans/2026-05-31-phase-7b-strands-bedrock.md`.

## Scope
- Strands Agents replaces inline_agent.py + bedrock.py
- Real Bedrock streaming via BedrockModel + AWSLWA
- /api/models endpoint + ModelPicker wired
- /regenerate endpoint + Conversation button
- Lambda timeout 30s → 5min; Bedrock IAM extended for Opus
- AgentCore Memory NOT wired (memory=None) — comes in 7c
- Idempotency replay payload carries real msg_ids
- Friendly model label in assistant header

## Out of scope (deferred to 7c/7d)
- AgentCore Memory event writes via Strands adapter — 7c
- Memory recall + auto-titling — 7d
EOF
)"
```

Capture the issue number (e.g. `#NN`).

- [ ] **Step 3: Open the PR**

```bash
gh pr create --base development \
  --title "feat(channel-7b): Strands + real Bedrock streaming + /models + /regenerate" \
  --body "$(cat <<'EOF'
Phase 7b of the Bedrock + AgentCore Memory chat backend
(spec: `docs/superpowers/specs/2026-05-30-bedrock-agentcore-chat-design.md`,
plan: `docs/superpowers/plans/2026-05-31-phase-7b-strands-bedrock.md`).

Closes #NN

## Summary

- New `chat_agent.py` constructs a `strands.Agent` with `BedrockModel`
  per turn; `strands_sse.py` translates Strands events to SSE.
- POST /messages streams real Bedrock output (replaces the 7a canned reply).
- New GET /api/models (server allowlist) + POST /api/chats/{id}/regenerate.
- ModelPicker fetches the allowlist; Conversation gets a Regenerate button.
- CDK: Lambda timeout 30s → 5 min; Bedrock IAM extended for Opus 4.7.
- Idempotency replay payload now carries real msg_ids.
- Friendly model label in assistant header.
- inline_agent.py + bedrock.py deleted (superseded by Strands).

## Out of scope (deferred)

- AgentCore Memory writes via Strands adapter — 7c
- Memory recall + auto-titling — 7d
- Strands recall spike documented at
  `docs/superpowers/specs/2026-05-31-strands-agentcore-memory-spike.md`

## Test plan

- [x] uv run inv pre-push (lint + typecheck + 300+ unit + 510+ vitest)
- [x] uv run inv test-integration
- [x] uv run inv synth
- [x] Manual smoke: real Bedrock reply streams; ModelPicker lists 3 models;
      Regenerate drops + re-streams; assistant header shows friendly label
- [ ] Wait for CI green
EOF
)"
gh pr merge --auto --squash --delete-branch
```

- [ ] **Step 4: Watch CI**

```bash
gh run watch
```

Expected: all checks green. Fix anything that fails, push (explicit refspec), re-watch.

---

## End-state checklist

After this plan is done, this is true:

- [ ] POST /api/chats/{id}/messages streams real Bedrock output via Strands.
- [ ] POST /api/chats/{id}/regenerate works.
- [ ] GET /api/models returns the server allowlist.
- [ ] ModelPicker shows three Anthropic models; selecting one changes the model id sent on send.
- [ ] Assistant turn headers show friendly labels.
- [ ] Lambda timeout is 5 min; Bedrock IAM covers Sonnet, Haiku, and Opus.
- [ ] inline_agent.py and bedrock.py are gone.
- [ ] Idempotency replay payload carries real msg_ids + full assistant text.
- [ ] Strands AgentCore Memory spike report is committed.
- [ ] inv pre-push green; inv test-integration green; inv synth green.
- [ ] AgentCore Memory still NOT wired (memory=None on the Agent).

## Open items intentionally deferred

- AgentCore Memory `CreateMemory` (lazy per-user) — **7c**.
- AgentCore Memory write integration via Strands adapter — **7c**.
- AgentCore Memory recall + system-prompt injection — **7d**.
- Auto-titling via cheap Haiku call — **7d**.
- `title_suggested` SSE event — **7d**.
- CloudWatch EMF metrics (`ChatTurnDurationMs`, `BedrockInvokeFailures`,
  `AgentCoreWriteFailures`) — recommended as a 7c follow-up once memory
  writes start, so divergence-rate metrics have something to measure.
- Cost-runaway alarms + abuse rate-limiting — recommended as a
  follow-up before non-dev users hit the system.
- Removing the now-orphan `bedrock:InvokeInlineAgent` IAM grant — small
  follow-up cleanup; harmless to leave in place.
