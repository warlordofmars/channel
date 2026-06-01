# Phase 8a — ListEvents-based recall implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pivot the recall hook from `RetrieveMemoryRecords` (async, broken in practice) to `ListSessions` + `ListEvents` (synchronous, works immediately). Back out the `SemanticMemoryStrategy` since it's no longer used. Restore the strong "agent surfaces planted facts in chat B" e2e assertion.

**Architecture:** `AgentCoreRecallHook` keeps its existing structure — same `BeforeInvocationEvent` subscription, same cache, same kill-switch, same fire-and-forget failure mode. The hook's `_get_or_fetch_records` method is rewritten to call `list_sessions(memoryId, actorId)`, filter out the current chat's session, cap at the 5 most recent, and fetch the 2 most recent events from each via `list_events(memoryId, actorId, sessionId, maxResults=2)`. `_format_recall_addendum` is rewritten to group events by session with a date header per session.

**Tech Stack:** boto3 `bedrock-agentcore` (ListSessions, ListEvents), Strands HookProvider, FastAPI streaming (unchanged), Playwright async API.

**Spec:** `docs/superpowers/specs/2026-05-31-phase-8a-listevents-recall-design.md` — read it before starting Task 1. Caps (5 sessions × 2 events × 120-char truncation), current-chat exclusion, and the strategy backout are spec decisions; don't reinvent them.

---

## File map

**Modify:**
- `src/channel/agents/recall.py` — rewrite `_get_or_fetch_records` + `_format_recall_addendum` + constants
- `src/channel/agents/memory.py` — remove `SemanticMemoryStrategy` from `CreateMemory`
- `tests/unit/test_recall.py` — drop semantic tests, add ListSessions/ListEvents tests
- `tests/unit/test_memory.py` — drop the strategy-presence assertion
- `tests/e2e/test_memory_recall_and_titling.py` — restore strong assertion in Test A
- `CHANGELOG.md`
- `CLAUDE.md`

**Create:** none — pure pivot of existing files.

---

## Task 1: Update constants + remove strategy from memory.py

**Files:**
- Modify: `src/channel/agents/recall.py`
- Modify: `src/channel/agents/memory.py`
- Modify: `tests/unit/test_memory.py`

Smallest-scope change first to land the constants + strategy backout, then build on top. Memory tests get updated atomically so existing CI stays green.

- [ ] **Step 1: Update recall constants**

In `src/channel/agents/recall.py`, replace the cap constants:

```python
# Remove:
_RECALL_SCORE_THRESHOLD: float = 0.7
_RECALL_TOP_K: int = 5

# Add:
_RECALL_MAX_SESSIONS: int = 5
_RECALL_EVENTS_PER_SESSION: int = 2
_RECALL_EVENT_TEXT_TRUNCATE: int = 120
```

Leave `_RECALL_CACHE_REFRESH_TURNS = 5` alone.

- [ ] **Step 2: Remove the SemanticMemoryStrategy block**

In `src/channel/agents/memory.py`, the `create_memory` call currently has:

```python
created = control.create_memory(
    name=name,
    memoryStrategies=[
        {
            "semanticMemoryStrategy": {
                "name": "channel_semantic_recall",
                "namespaces": [
                    "/strategies/{memoryStrategyId}/actors/{actorId}",
                ],
            },
        },
    ],
    eventExpiryDuration=90,
)
```

Replace with:

```python
# Phase 8a: SemanticMemoryStrategy retired — its ingestion lag (hours
# in real use) made RetrieveMemoryRecords unusable. Recall now reads
# raw events via ListSessions + ListEvents (see agents/recall.py).
# Strategy-less memories provision faster (no strategy resources to
# spin up) and cost less.
created = control.create_memory(
    name=name,
    memoryStrategies=[],
    eventExpiryDuration=90,
)
```

- [ ] **Step 3: Update the memory unit test**

In `tests/unit/test_memory.py`, remove the strategy-presence assertion from `test_get_or_create_memory_creates_when_absent`:

```python
# Remove these lines:
strategies = kwargs["memoryStrategies"]
assert len(strategies) == 1
assert "semanticMemoryStrategy" in strategies[0]
assert strategies[0]["semanticMemoryStrategy"]["namespaces"] == [
    "/strategies/{memoryStrategyId}/actors/{actorId}",
]

# Optionally add a regression-guard assertion:
assert kwargs["memoryStrategies"] == [], (
    "Phase 8a backed out SemanticMemoryStrategy; recall reads raw "
    "events via ListSessions + ListEvents instead."
)
```

- [ ] **Step 4: Run impacted tests**

```bash
uv run pytest tests/unit/test_memory.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/recall.py src/channel/agents/memory.py tests/unit/test_memory.py
git commit -m "feat(channel-8a): swap recall constants + remove SemanticMemoryStrategy"
```

---

## Task 2: Rewrite `_get_or_fetch_records` for ListSessions + ListEvents

**Files:**
- Modify: `src/channel/agents/recall.py`
- Modify: `tests/unit/test_recall.py`

This is the core change. Replace the single `RetrieveMemoryRecords` RPC with a two-step `ListSessions` → per-session `ListEvents` flow.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_recall.py`, replace the existing `test_hook_injects_records_into_system_prompt_on_first_turn`, `test_hook_filters_records_below_score_threshold` and any other RetrieveMemoryRecords-specific tests with the new flow. Keep cache, sanitization, kill-switch, and failure-swallow tests.

```python
@pytest.mark.asyncio
async def test_hook_lists_sessions_and_events_excluding_current_chat():
    """Recall iterates this actor's sessions, drops the current chat,
    fetches the last 2 events per session, returns the aggregate."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {
        "sessionSummaries": [
            {"sessionId": "current-chat", "createdAt": "2026-05-31T20:00:00Z"},
            {"sessionId": "prior-1", "createdAt": "2026-05-31T19:00:00Z"},
            {"sessionId": "prior-2", "createdAt": "2026-05-31T18:00:00Z"},
        ],
    }
    fake_client.list_events.side_effect = [
        {
            "events": [
                {
                    "sessionId": "prior-1",
                    "eventTimestamp": "2026-05-31T19:00:30Z",
                    "payload": [
                        {"conversational": {"role": "USER", "content": {"text": "hello from prior-1"}}},
                        {"conversational": {"role": "ASSISTANT", "content": {"text": "hi"}}},
                    ],
                },
            ],
        },
        {
            "events": [
                {
                    "sessionId": "prior-2",
                    "eventTimestamp": "2026-05-31T18:00:30Z",
                    "payload": [
                        {"conversational": {"role": "USER", "content": {"text": "hello from prior-2"}}},
                        {"conversational": {"role": "ASSISTANT", "content": {"text": "hi"}}},
                    ],
                },
            ],
        },
    ]
    hook = AgentCoreRecallHook(
        memory_id="m-1", actor_id="user_abc", client=fake_client,
    )
    event = _fake_before_event(user_text="anything")

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(event, chat_id="current-chat")

    # ListSessions called once, scoped to actor.
    fake_client.list_sessions.assert_called_once_with(
        memoryId="m-1", actorId="user_abc",
    )
    # ListEvents called once per prior session, NOT for the current chat.
    assert fake_client.list_events.call_count == 2
    session_ids_queried = {
        call.kwargs["sessionId"] for call in fake_client.list_events.call_args_list
    }
    assert session_ids_queried == {"prior-1", "prior-2"}
    # System prompt grew with content from both prior sessions.
    sys_text = event.messages[0]["content"][0]["text"]
    assert "hello from prior-1" in sys_text
    assert "hello from prior-2" in sys_text


@pytest.mark.asyncio
async def test_hook_caps_sessions_at_max():
    """8 sessions returned → only the 5 most-recent get ListEvents'd."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {
        "sessionSummaries": [
            {"sessionId": f"s{i}", "createdAt": f"2026-05-{30 - i:02d}T00:00:00Z"}
            for i in range(8)
        ],
    }
    fake_client.list_events.return_value = {"events": []}
    hook = AgentCoreRecallHook(
        memory_id="m", actor_id="a", client=fake_client,
    )
    event = _fake_before_event(user_text="anything")

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(event, chat_id="not-in-list")

    assert fake_client.list_events.call_count == 5  # _RECALL_MAX_SESSIONS


@pytest.mark.asyncio
async def test_hook_caps_events_per_session():
    """ListEvents is called with maxResults=_RECALL_EVENTS_PER_SESSION."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {
        "sessionSummaries": [{"sessionId": "s1", "createdAt": "2026-05-31T00:00:00Z"}],
    }
    fake_client.list_events.return_value = {"events": []}
    hook = AgentCoreRecallHook(
        memory_id="m", actor_id="a", client=fake_client,
    )
    event = _fake_before_event(user_text="anything")

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(event, chat_id="not-in-list")

    call = fake_client.list_events.call_args
    assert call.kwargs["maxResults"] == _RECALL_EVENTS_PER_SESSION


@pytest.mark.asyncio
async def test_hook_emits_no_addendum_when_actor_has_no_prior_sessions():
    """0 prior sessions → no addendum, no ListEvents calls."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {"sessionSummaries": []}
    hook = AgentCoreRecallHook(
        memory_id="m", actor_id="a", client=fake_client,
    )
    event = _fake_before_event(
        user_text="hello", system_text="You are Channel.",
    )

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(event, chat_id="any")

    fake_client.list_events.assert_not_called()
    # System prompt unchanged.
    assert event.messages[0]["content"][0]["text"] == "You are Channel."


@pytest.mark.asyncio
async def test_hook_emits_no_addendum_when_only_session_is_current_chat():
    """1 session = the current chat → exclusion leaves 0 candidates,
    no addendum, no ListEvents calls."""
    fake_client = MagicMock()
    fake_client.list_sessions.return_value = {
        "sessionSummaries": [
            {"sessionId": "current", "createdAt": "2026-05-31T00:00:00Z"},
        ],
    }
    hook = AgentCoreRecallHook(
        memory_id="m", actor_id="a", client=fake_client,
    )
    event = _fake_before_event(
        user_text="hello", system_text="You are Channel.",
    )

    with patch("channel.agents.recall.record_recall_outcome", new=AsyncMock()):
        await hook._on_before_invocation_async(event, chat_id="current")

    fake_client.list_events.assert_not_called()
    assert event.messages[0]["content"][0]["text"] == "You are Channel."
```

Drop the obsolete tests:
- `test_hook_injects_records_into_system_prompt_on_first_turn`
- `test_hook_filters_records_below_score_threshold`

Keep these (they still apply — cache + sanitization + kill-switch are unchanged):
- `test_hook_registers_before_invocation_callback_only`
- `test_hook_reuses_cached_records_for_next_5_turns`
- `test_hook_refreshes_cache_after_5_turns`
- `test_hook_per_chat_cache_keys`
- `test_hook_swallows_retrieve_failures_and_emits_failure_metric` (rename: `_swallows_list_failures_`)
- `test_hook_short_circuits_when_kill_switch_off`
- `test_hook_before_invocation_fires_and_forgets`
- `test_hook_default_client_is_bedrock_agentcore`
- The "actor_id sanitization at boundary" test

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_recall.py -v -k "lists_sessions_and_events"`
Expected: `AttributeError` or KeyError (mock not configured for the new API).

- [ ] **Step 3: Implement**

In `src/channel/agents/recall.py`, replace `_get_or_fetch_records`:

```python
async def _get_or_fetch_records(
    self, *, user_message: str, chat_id: str,
) -> list[dict[str, Any]]:
    """Return cached records when fresh; refetch when stale or missing.

    Phase 8a: synchronous recall via ListSessions + ListEvents per
    session. Excludes the current chat (PR #73 already feeds that
    history into Strands via ``Agent(messages=...)``). Caps at
    ``_RECALL_MAX_SESSIONS`` × ``_RECALL_EVENTS_PER_SESSION`` to
    bound prompt size.

    ``user_message`` is no longer used for retrieval (we don't do
    semantic search anymore) — kept on the signature for cache
    parity and to match the BeforeInvocationEvent contract.
    """
    del user_message  # no longer used for retrieval
    key = (self._actor_id, chat_id)
    cache_entry = _recall_cache.get(key)
    if cache_entry is not None and cache_entry.age < _RECALL_CACHE_REFRESH_TURNS:
        cache_entry.age += 1
        return cache_entry.records

    # Step 1: list this actor's sessions, exclude the current chat.
    sessions_resp = await asyncio.to_thread(
        self._client.list_sessions,
        memoryId=self._memory_id,
        actorId=self._actor_id,
    )
    sessions = sessions_resp.get("sessionSummaries", [])
    prior_sessions = [
        s for s in sessions if s.get("sessionId") != chat_id
    ][:_RECALL_MAX_SESSIONS]

    # Step 2: fetch the last K events from each prior session.
    aggregated: list[dict[str, Any]] = []
    for session in prior_sessions:
        events_resp = await asyncio.to_thread(
            self._client.list_events,
            memoryId=self._memory_id,
            actorId=self._actor_id,
            sessionId=session["sessionId"],
            maxResults=_RECALL_EVENTS_PER_SESSION,
        )
        for event in events_resp.get("events", []):
            aggregated.append({
                "sessionId": session["sessionId"],
                "createdAt": session.get("createdAt", ""),
                "payload": event.get("payload", []),
            })

    _recall_cache[key] = CacheEntry(records=aggregated, age=1)
    return aggregated
```

- [ ] **Step 4: Run tests + coverage**

Run: `uv run pytest tests/unit/test_recall.py --cov=channel.agents.recall --cov-report=term-missing`
Expected: all PASS, 100%.

- [ ] **Step 5: Commit**

```bash
git add src/channel/agents/recall.py tests/unit/test_recall.py
git commit -m "feat(channel-8a): _get_or_fetch_records via ListSessions + ListEvents"
```

---

## Task 3: Rewrite `_format_recall_addendum` for session-grouped output

**Files:**
- Modify: `src/channel/agents/recall.py`
- Modify: `tests/unit/test_recall.py`

The new records have shape `{sessionId, createdAt, payload}` where `payload` is the AgentCore conversational message list. Format as Markdown groups.

- [ ] **Step 1: Write the failing tests**

Replace the existing `_format_recall_addendum` tests:

```python
def test_format_recall_addendum_groups_records_by_session_with_date_header():
    records = [
        {
            "sessionId": "s1",
            "createdAt": "2026-05-31T19:00:00Z",
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": "i love sage green"}}},
                {"conversational": {"role": "ASSISTANT", "content": {"text": "sage is great"}}},
            ],
        },
        {
            "sessionId": "s2",
            "createdAt": "2026-05-31T18:00:00Z",
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": "building Nightfall"}}},
                {"conversational": {"role": "ASSISTANT", "content": {"text": "cool engine name"}}},
            ],
        },
    ]
    result = _format_recall_addendum(records)

    assert "## What we've talked about before" in result
    # Date header per session.
    assert "Earlier conversation (2026-05-31)" in result
    # Two session blocks.
    assert result.count("Earlier conversation") == 2
    # Role mapping.
    assert "- You: i love sage green" in result
    assert "- Me: sage is great" in result
    assert "- You: building Nightfall" in result
    assert "- Me: cool engine name" in result


def test_format_recall_addendum_truncates_long_text():
    long_text = "A" * 500
    records = [
        {
            "sessionId": "s1",
            "createdAt": "2026-05-31T00:00:00Z",
            "payload": [
                {"conversational": {"role": "USER", "content": {"text": long_text}}},
            ],
        },
    ]
    result = _format_recall_addendum(records)
    # Each event text capped at _RECALL_EVENT_TEXT_TRUNCATE chars
    # (plus a trailing ellipsis indicator).
    assert "A" * 500 not in result
    assert "..." in result


def test_format_recall_addendum_returns_empty_string_for_no_records():
    assert _format_recall_addendum([]) == ""


def test_format_recall_addendum_skips_records_with_no_payload_text():
    records = [
        {
            "sessionId": "s1",
            "createdAt": "2026-05-31T00:00:00Z",
            "payload": [
                {"conversational": {"role": "USER", "content": {}}},  # no text
                {"conversational": {"role": "ASSISTANT", "content": {"text": ""}}},
            ],
        },
    ]
    # All payload entries unusable → no bullets → session block dropped.
    result = _format_recall_addendum(records)
    assert result == ""
```

- [ ] **Step 2: Implement**

Replace `_format_recall_addendum` in `recall.py`:

```python
_RECALL_HEADING = "## What we've talked about before"
_RECALL_ROLE_MAP = {"USER": "You", "ASSISTANT": "Me"}


def _format_recall_addendum(records: list[dict[str, Any]]) -> str:
    """Render aggregated ListEvents output as a Markdown addendum.

    Records are grouped by ``sessionId``; each group gets a header
    derived from ``createdAt`` (date only — time-of-day is noise).
    Within each group, AgentCore ``payload[].conversational``
    messages become turn bullets:

        ## What we've talked about before

        **Earlier conversation (2026-05-31)**
        - You: i love sage green
        - Me: sage is great

    Returns the empty string if no records have usable text.
    """
    if not records:
        return ""

    # Group by sessionId, preserving the order in ``records`` (which is
    # already most-recent first from ListSessions).
    groups: dict[str, dict[str, Any]] = {}
    for rec in records:
        sid = rec.get("sessionId", "")
        if not sid:
            continue
        group = groups.setdefault(
            sid, {"createdAt": rec.get("createdAt", ""), "bullets": []},
        )
        for msg in rec.get("payload", []):
            conv = msg.get("conversational") or {}
            role_raw = conv.get("role", "")
            text = (conv.get("content") or {}).get("text", "")
            if not text:
                continue
            role_label = _RECALL_ROLE_MAP.get(role_raw, role_raw or "?")
            if len(text) > _RECALL_EVENT_TEXT_TRUNCATE:
                text = text[:_RECALL_EVENT_TEXT_TRUNCATE] + "..."
            group["bullets"].append(f"- {role_label}: {text}")

    blocks: list[str] = []
    for group in groups.values():
        if not group["bullets"]:
            continue
        date = group["createdAt"][:10] if group["createdAt"] else "earlier"
        blocks.append(
            f"**Earlier conversation ({date})**\n" + "\n".join(group["bullets"])
        )

    if not blocks:
        return ""
    return _RECALL_HEADING + "\n\n" + "\n\n".join(blocks)
```

- [ ] **Step 3: Run tests + coverage**

Run: `uv run pytest tests/unit/test_recall.py --cov=channel.agents.recall --cov-report=term-missing`
Expected: 100%, all pass.

- [ ] **Step 4: Commit**

```bash
git add src/channel/agents/recall.py tests/unit/test_recall.py
git commit -m "feat(channel-8a): _format_recall_addendum groups events by session"
```

---

## Task 4: Restore strong assertion in Playwright e2e

**Files:**
- Modify: `tests/e2e/test_memory_recall_and_titling.py`

7d weakened Test A to just "stream completes" because semantic recall returned empty. 8a should be able to assert specific keywords surface again.

- [ ] **Step 1: Rewrite Test A**

```python
async def test_cross_session_recall_surfaces_planted_facts() -> None:
    """Plant facts in chat A; assert chat B's agent recalls them.

    Phase 8a: recall is synchronous (ListSessions + ListEvents), so
    facts written 3s ago are immediately queryable. No multi-minute
    wait required (the SemanticMemoryStrategy ingestion lag from 7d
    is gone)."""
    ui_url = os.environ.get("STARTER_UI_URL")
    api_url = os.environ.get("STARTER_API_URL", "http://localhost:8001")
    if not ui_url:
        pytest.skip("STARTER_UI_URL not set — run via `inv e2e-local`")

    tag = f"e2e-8a-recall-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email = f"{tag}@example.com"
    jwt = _mint_jwt_via_bypass(api_url, email)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            # Chat A — plant two facts.
            chat_a_id, _ = await _drive_chat(
                browser, ui_url, jwt,
                messages=[
                    "I'm building a chess engine called Nightfall.",
                    "My favourite colour is sage green.",
                ],
            )
            # Brief eventual-consistency window for CreateEvent.
            await asyncio.sleep(3)
            events = _list_events(api_url, jwt, chat_a_id, limit=10)
            assert len(events) == 2, (
                f"expected 2 events for chat A, got {len(events)}"
            )

            # Chat B — recall must surface Nightfall + sage.
            _chat_b_id, b_replies = await _drive_chat(
                browser, ui_url, jwt,
                messages=[
                    "What was the project I'm working on?",
                    "And the colour I like?",
                ],
            )
        finally:
            await browser.close()

    # Strong deterministic-keyword assertions (the 7d weakening is
    # rolled back now that recall is synchronous).
    assert any("nightfall" in r.lower() for r in b_replies), (
        f"chat B should mention 'Nightfall' (recall worked); got: {b_replies!r}"
    )
    assert any("sage" in r.lower() for r in b_replies), (
        f"chat B should mention 'sage' (recall worked); got: {b_replies!r}"
    )
```

The auto-title test (Test B) and helpers stay unchanged.

- [ ] **Step 2: Verify syntax**

```bash
uv run pytest tests/e2e/test_memory_recall_and_titling.py --collect-only
```

Expected: 2 tests collected, no errors.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_memory_recall_and_titling.py
git commit -m "test(channel-8a): restore strong cross-session-recall assertion"
```

---

## Task 5: Local verification dry-run (BEFORE pre-push, BEFORE PR)

The mandatory live-stack pass. Skipping this is exactly what the user's memory note warns against.

- [ ] **Step 1: Start the stack**

```bash
uv run inv dev
```

Wait for `Uvicorn running on http://0.0.0.0:8001` and Vite ready.

- [ ] **Step 2: Reset the local DDB**

```bash
uv run python scripts/reset_dev_table.py
```

- [ ] **Step 3: Delete the local AgentCore memory (it has the strategy from 7d)**

The strategy backout doesn't auto-migrate existing memories. To verify the strategy-less path:

```bash
aws bedrock-agentcore-control list-memories --region us-east-1 \
  --query 'memories[?contains(id, `channel_jc`)] | [*].id' --output text
# Delete each:
aws bedrock-agentcore-control delete-memory --memory-id <id> --region us-east-1
# Wait for deletion:
until [ "$(aws bedrock-agentcore-control list-memories --region us-east-1 \
  --query 'memories[?contains(id, `channel_jc`)] | length(@)' --output text)" = "0" ]; do
  sleep 5
done
```

Restart `inv dev` so the Lambda's cached memoryId clears.

- [ ] **Step 4: Run the e2e**

```bash
uv run inv e2e-local --tests tests/e2e/test_memory_recall_and_titling.py -n 3
```

Expected: 3 consecutive passes. Test A asserts the strong "Nightfall" / "sage" surface.

- [ ] **Step 5: Manual spot-check via the UI**

Open `http://localhost:5173/app`, sign in with a test email. Plant: `"I love Italian food and I'm allergic to peanuts."` in chat A. Start chat B: `"What allergies should I avoid?"` — agent should mention peanuts immediately (no wait).

- [ ] **Step 6: Tear down**

Ctrl-C the `inv dev` terminal.

---

## Task 6: CHANGELOG + CLAUDE.md sync

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: CHANGELOG entry**

Add to `[Unreleased]`:

```markdown
### Changed

- Phase 8a — memory recall now uses synchronous `ListSessions` +
  `ListEvents` instead of `RetrieveMemoryRecords`. AgentCore's
  `SemanticMemoryStrategy` had a multi-hour ingestion lag in real
  use, leaving recall empty long after events were written. The
  new path retrieves raw prior-chat events directly (cap: 5
  sessions × 2 events each) and works immediately. New Memory
  resources are no longer created with a strategy.
```

- [ ] **Step 2: CLAUDE.md `## AgentCore Memory` → `### Recall (Phase 7d)` update**

Replace the existing 7d recall description with the 8a behaviour:

```markdown
### Recall (Phase 8a; pivoted from 7d)

- **`AgentCoreRecallHook`** (`src/channel/agents/recall.py`)
  subscribes to `BeforeInvocationEvent`. Per turn, queries the
  actor's prior chats via `ListSessions` + per-session
  `ListEvents`, formats the aggregate as a Markdown addendum
  grouped by session with a date header, mutates
  `event.messages[0]`.
- **Caps** — `_RECALL_MAX_SESSIONS = 5` most-recent prior
  sessions; `_RECALL_EVENTS_PER_SESSION = 2` most-recent events
  per session; `_RECALL_EVENT_TEXT_TRUNCATE = 120` chars per
  quoted turn. Worst-case prompt overhead ~2.4 KB.
- **Current chat excluded** — PR #73 already feeds the current
  chat's DDB history into Strands; the recall hook drops that
  `sessionId` from the ListSessions result to avoid double-feeding.
- **5-turn cache** — keyed by `(actor_id, chat_id)`, module-level.
  Cold-start invalidates.
- **Kill-switch** — `STARTER_RECALL_ENABLED=0` short-circuits.
- **History**: Phase 7d implemented this via `RetrieveMemoryRecords`
  + `SemanticMemoryStrategy`. The strategy's async ingestion lag
  (hours in real use) made recall empty for too long, so 8a pivoted
  to raw-event reads. The strategy is no longer attached to new
  memories.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs(channel-8a): CHANGELOG + CLAUDE.md sync — ListEvents recall pivot"
```

---

## Task 7: Pre-push + file issue + open PR

- [ ] **Step 1: Run pre-push gate**

```bash
uv run inv pre-push
```

Expected: all green.

- [ ] **Step 2: File the issue**

```bash
gh issue create \
  --title "feat(channel-8a): synchronous ListEvents recall (replaces 7d semantic search)" \
  --label "status:ready,priority:p1,size:s,enhancement,agent-safe" \
  --body "$(cat <<'EOF'
Pivots Phase 7d's recall path from `RetrieveMemoryRecords` +
`SemanticMemoryStrategy` (async, broken in practice due to ingestion
lag) to synchronous `ListSessions` + `ListEvents`.

Design: docs/superpowers/specs/2026-05-31-phase-8a-listevents-recall-design.md
Plan: docs/superpowers/plans/2026-05-31-phase-8a-listevents-recall.md

## Files to touch

- src/channel/agents/recall.py
- src/channel/agents/memory.py
- tests/unit/test_recall.py
- tests/unit/test_memory.py
- tests/e2e/test_memory_recall_and_titling.py
- CHANGELOG.md
- CLAUDE.md
- docs/superpowers/specs/2026-05-31-phase-8a-listevents-recall-design.md
- docs/superpowers/plans/2026-05-31-phase-8a-listevents-recall.md
EOF
)"
```

NOTE: `agent-safe` IS applicable here — no infra/IAM/workflow surface touched, only the recall hook + tests + docs. Per CLAUDE.md the agent can run Copilot review and auto-merge on green.

- [ ] **Step 3: Branch + push + open PR**

```bash
git fetch origin
git rebase origin/development
git log --oneline origin/development..HEAD

git push -u origin feat/channel-8a-listevents-recall:feat/channel-8a-listevents-recall

gh pr create --base development \
  --title "feat(channel-8a): synchronous ListEvents recall" \
  --body "$(cat <<'EOF'
## Summary

- Pivots the recall hook from 7d's `RetrieveMemoryRecords` + `SemanticMemoryStrategy` (async, hours of ingestion lag, broken in real use) to synchronous `ListSessions` + per-session `ListEvents`.
- Same hook surface, same cache, same kill-switch — only `_get_or_fetch_records` and `_format_recall_addendum` change shape.
- Caps: 5 most-recent prior sessions × 2 most-recent events per session × 120-char truncation. Current chat excluded so we don't double-feed PR #73's DDB-history seed.
- `SemanticMemoryStrategy` backed out of `CreateMemory` — we no longer use it.
- Layer-2 e2e flips Test A back to the strong "agent surfaces planted facts in chat B" assertion that 7d had to weaken.

Closes #<NNN-from-step-2>

## Test plan

- [x] Layer 1 — `inv pre-push` green, 100% coverage on `recall.py`.
- [x] Layer 2 — Playwright e2e (`inv e2e-local --tests tests/e2e/test_memory_recall_and_titling.py -n 3`) passes against personal AWS env. Test A asserts `"Nightfall"` and `"sage"` surface in chat B.
- [ ] Layer 3 — after merge + dev deploy: plant `"my favorite colour is blue"` at https://channel-dev.warlordofmars.net/app in a fresh chat A; start chat B, ask `"what's my favourite colour?"` — agent answers `"blue"`. (The demo we've been blocked on.)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"

gh pr merge --auto --squash --delete-branch
```

- [ ] **Step 4: Watch CI**

```bash
gh run watch
```

- [ ] **Step 5: Layer 3 verification (after dev deploy)**

After the PR auto-merges and dev deploys:

1. Open `https://channel-dev.warlordofmars.net/app` as your real user.
2. Plant `"my favourite colour is blue"` in a fresh chat A.
3. Wait ~5 seconds for `CreateEvent` to commit.
4. Start chat B. Ask `"what's my favourite colour?"`.
5. Expected: agent says `"blue"`. This is the demo we couldn't ship under 7d.

If the agent still says it doesn't know:
- Check `aws bedrock-agentcore list-actors --memory-id <dev-memory-id> --region us-east-1` — your actor should appear.
- Check `aws bedrock-agentcore list-sessions --memory-id <dev-memory-id> --actor-id johncarter_warlordofmars_net --region us-east-1` — should show chat A's session.
- Check `aws logs tail .../ChannelStack-dev-Api... --filter-pattern recall_failed --since 2m` for swallowed errors.

---

## Done criteria

- ✅ `recall.py` uses `ListSessions` + `ListEvents`; no remaining
  `retrieve_memory_records` calls.
- ✅ `memory.py` no longer attaches `SemanticMemoryStrategy`.
- ✅ 100% coverage on `recall.py` + `memory.py`.
- ✅ Playwright e2e Test A asserts strong deterministic keyword
  surfaces (`"Nightfall"`, `"sage"`); passes 3 consecutive runs.
- ✅ CHANGELOG + CLAUDE.md updated.
- ✅ Live test on dev: "favourite colour is blue" in chat A,
  recalled in chat B.
