# Chat rename + delete — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add user-facing rename + delete actions to each chat in the sidebar Recents list, via a hover-revealed `⋮` button that opens a popover menu. Rename + Delete are fully wired; Pin / Change project / Remove from project render as disabled placeholders. Delete is permanent — wipes both DynamoDB rows AND AgentCore Memory events.

**Architecture:** Hover-revealed `<button>` per `.recent` row → click opens a `ChatRowMenu` popover (same `<backdrop>+<pop>` pattern as `ModelPicker` / `AccountPopover`) → Rename opens a `RenameChatModal`, Delete opens a `DeleteChatModal` (both consume a new shared `Modal.jsx` primitive that owns Esc + backdrop dismissal + focus trap). New `DELETE /api/chats/{chat_id}` endpoint deletes message rows + chat-index row from DDB and best-effort wipes the AgentCore session's events (failure is logged + counted but does NOT fail the user's delete).

**Tech Stack:** React (Vite) + react-router-dom (frontend), FastAPI + boto3 (backend), DynamoDB single-table, AWS Bedrock AgentCore Memory.

**Spec:** `docs/superpowers/specs/2026-06-01-chat-rename-delete-design.md` — read it before starting Task 1. Menu items, delete semantics, modal flow, IAM (already granted), and the `--danger` token addition are spec decisions; don't reinvent them.

---

## File map

**Create:**
- `ui/src/components/Modal.jsx` — shared modal primitive
- `ui/src/components/Modal.test.jsx`
- `ui/src/app/ChatRowMenu.jsx` — per-row popover with 5 items
- `ui/src/app/ChatRowMenu.test.jsx`
- `ui/src/app/RenameChatModal.jsx` — rename dialog
- `ui/src/app/RenameChatModal.test.jsx`
- `ui/src/app/DeleteChatModal.jsx` — delete confirm dialog
- `ui/src/app/DeleteChatModal.test.jsx`
- `tests/e2e/test_chat_management.py` — Playwright cover

**Modify:**
- `src/channel/storage.py` — add `delete_chat` helper
- `src/channel/api/chats.py` — add `DELETE /api/chats/{chat_id}` endpoint
- `src/channel/metrics.py` — add `record_chat_delete_memory_wipe_outcome`
- `ui/src/api.js` — add `deleteChat`
- `ui/src/hooks/useChatList.js` — add `deleteChat`
- `ui/src/hooks/ChatsContext.jsx` — re-export `deleteChat`
- `ui/src/app/Sidebar.jsx` — per-row menu button + modal hosting
- `ui/src/components/Icon.jsx` — `pencil`, `trash`, `more-vertical`, `folder-x`
- `ui/src/styles/channel.css` — `--danger` token (light + dark)
- `ui/src/styles/app.css` — menu button hover, popover, modal, danger button styles
- `tests/unit/test_storage.py` — `delete_chat` coverage
- `tests/unit/test_chats_api.py` — DELETE endpoint coverage
- `tests/unit/test_metrics.py` — new metric signature lock
- `ui/src/hooks/useChatList.test.js` — `deleteChat` coverage
- `ui/src/app/Sidebar.test.jsx` — menu button + modal hosting coverage
- `ui/src/components/Icon.test.jsx` — new icons render
- `CHANGELOG.md`
- `CLAUDE.md`

---

## Task 1: Add `--danger` CSS token

**Files:**
- Modify: `ui/src/styles/channel.css`

The new token is needed by `.btn-danger` (Task 9's DeleteChatModal) and `.chat-row-menu-item.danger` (Task 10's ChatRowMenu). Add it first so later tasks just consume it.

- [ ] **Step 1: Add the token to both themes**

Open `ui/src/styles/channel.css`. The light theme block defines `--accent` at line 32; add `--danger` immediately after the accent triplet. The dark theme block defines `--accent` at line 54; do the same. The exact OKLCH values are spec-mandated.

```css
/* In :root (light) block, after --accent-soft: */
--danger:      oklch(0.55 0.18 25);
--danger-soft: oklch(0.93 0.05 25);

/* In @media (prefers-color-scheme: dark) :root block, after --accent-soft: */
--danger:      oklch(0.70 0.16 25);
--danger-soft: oklch(0.33 0.06 25);
```

(The `-soft` variants give the `:hover` state of `.btn-danger` a tinted-but-not-bright shade; needed by Task 9's CSS.)

- [ ] **Step 2: Verify it parses**

Run: `cd ui && npx vite build --mode development 2>&1 | tail -3`
Expected: build succeeds (no CSS parse error).

- [ ] **Step 3: Commit**

```bash
git add ui/src/styles/channel.css
git commit -m "feat(channel-rd): add --danger CSS token for destructive UI"
```

---

## Task 2: Add new icons to `Icon.jsx`

**Files:**
- Modify: `ui/src/components/Icon.jsx`
- Modify: `ui/src/components/Icon.test.jsx`

Four new glyphs: `pencil` (Rename), `trash` (Delete), `more-vertical` (the dots button on hover), `folder-x` (Remove from project placeholder). All 24×24 stroke-only to match the existing set.

- [ ] **Step 1: Write the failing test**

Open `ui/src/components/Icon.test.jsx`. Find the existing "renders by name" parameterised test or equivalent. Add the new names to its parameter list. If the file is new-style with a single render-all test, append:

```jsx
import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import Icon from "./Icon.jsx";

describe("Icon — new chat-management glyphs", () => {
  it.each(["pencil", "trash", "more-vertical", "folder-x"])(
    "renders %s without crashing",
    (name) => {
      const { container } = render(<Icon name={name} size={18} />);
      const svg = container.querySelector("svg");
      expect(svg).not.toBeNull();
      expect(svg.querySelector("path, rect, circle")).not.toBeNull();
    },
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && npx vitest run src/components/Icon.test.jsx -t "new chat-management glyphs"`
Expected: 4 failures — `<svg>` either renders nothing (unknown name returns `null`) or returns a default-case element.

- [ ] **Step 3: Add the four icons in `Icon.jsx`**

Locate the `switch (name)` block (around line 24-63). Add the following `case` entries, alphabetised among neighbours:

```jsx
case "pencil":       return <svg {...p}><path d="M4 20h4L19 9a2 2 0 0 0-3-3L5 16z"/><path d="M14 7l3 3"/></svg>;
case "trash":        return <svg {...p}><path d="M4 7h16M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13M10 11v7M14 11v7"/></svg>;
case "more-vertical":return <svg {...p}><circle cx="12" cy="5" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="12" cy="19" r="1.4"/></svg>;
case "folder-x":     return <svg {...p}><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.2H19.5A1.5 1.5 0 0 1 21 9.7V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 13l6 4M15 13l-6 4"/></svg>;
```

Note: `pencil` reuses the `write` glyph from line 41 — that's fine, they're synonyms in this codebase. If you want them to differ, keep `pencil` as written here (with the same path); the menu label "Rename" carries the meaning.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && npx vitest run src/components/Icon.test.jsx`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/Icon.jsx ui/src/components/Icon.test.jsx
git commit -m "feat(channel-rd): add pencil / trash / more-vertical / folder-x icons"
```

---

## Task 3: Backend storage helper `delete_chat`

**Files:**
- Modify: `src/channel/storage.py`
- Modify: `tests/unit/test_storage.py`

Pure DDB delete: paginates the chat's message rows, batch-deletes in chunks of 25 (DDB batch-write limit), then deletes the chat-index row.

- [ ] **Step 1: Write the failing test**

Open `tests/unit/test_storage.py`. Find the section for chat-related tests (near the existing `test_patch_chat_*` tests). Append:

```python
def test_delete_chat_removes_message_rows_and_chat_index_row(monkeypatch, ddb_table):
    """delete_chat removes EVERY message row plus the chat-index row.
    
    Uses a chat with 26 messages to cross the 25-row DDB batch boundary
    — guards against off-by-one in the batched-delete loop.
    """
    from channel import storage
    chat = storage.create_chat(user_id="u1", title="t", model_default="claude-sonnet-4-6")
    # Plant 26 fake message rows.
    table = storage._get_table()
    for i in range(26):
        table.put_item(Item={
            "PK": f"CHAT#{chat.chat_id}",
            "SK": f"MSG#2026-06-01T00:00:00.{i:06d}#m{i}",
            "msg_id": f"m{i}",
            "text": f"hello {i}",
            "role": "user",
        })

    storage.delete_chat(user_id="u1", chat=chat)

    # All message rows gone.
    msg_query = table.query(
        KeyConditionExpression=(
            Key("PK").eq(f"CHAT#{chat.chat_id}") & Key("SK").begins_with("MSG#")
        ),
    )
    assert msg_query["Count"] == 0, (
        f"expected 0 message rows after delete; got {msg_query['Count']}"
    )

    # Chat-index row gone.
    idx = table.get_item(Key={
        "PK": "USER#u1",
        "SK": storage._chat_index_sk(chat.created_at, chat.chat_id),
    })
    assert "Item" not in idx, "chat-index row still present after delete"


def test_delete_chat_is_idempotent(ddb_table):
    """Calling delete_chat twice doesn't raise — DDB delete is naturally idempotent."""
    from channel import storage
    chat = storage.create_chat(user_id="u1", title="t", model_default="claude-sonnet-4-6")
    storage.delete_chat(user_id="u1", chat=chat)
    storage.delete_chat(user_id="u1", chat=chat)  # MUST NOT raise
```

Make sure `from boto3.dynamodb.conditions import Key` is imported at the top of the file (it likely already is — check first).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_storage.py -k delete_chat -v`
Expected: 2 failures — `AttributeError: module 'channel.storage' has no attribute 'delete_chat'`.

- [ ] **Step 3: Implement `delete_chat` in `src/channel/storage.py`**

Add this function immediately after the existing `patch_chat` (which ends around line 235):

```python
def delete_chat(*, user_id: str, chat: Chat) -> None:
    """Permanently delete a chat: all its message rows + the chat-index row.

    Message rows live at ``PK=CHAT#{chat_id}, SK begins_with MSG#`` and
    are paginated + batch-deleted in chunks of 25 (DDB batch-write
    limit). The chat-index row lives at
    ``PK=USER#{user_id}, SK=CHAT#{created_at}#{chat_id}`` and is a
    single delete.

    DDB ``DeleteItem`` is naturally idempotent — calling this twice for
    the same chat is safe (the second call finds nothing to delete and
    no-ops). The API layer should still call this only once per user
    action; idempotency is a defence-in-depth guarantee, not a feature
    to lean on.
    """
    table = _get_table()
    # 1. Paginate + batch-delete all message rows.
    last_evaluated_key: dict[str, Any] | None = None
    while True:
        query_kwargs: dict[str, Any] = {
            "KeyConditionExpression": (
                Key("PK").eq(f"CHAT#{chat.chat_id}") & Key("SK").begins_with("MSG#")
            ),
            "ProjectionExpression": "PK, SK",  # don't fetch payloads we'll just throw away
        }
        if last_evaluated_key:
            query_kwargs["ExclusiveStartKey"] = last_evaluated_key
        page = table.query(**query_kwargs)
        items = page.get("Items", [])
        if items:
            with table.batch_writer() as bw:
                for it in items:
                    bw.delete_item(Key={"PK": it["PK"], "SK": it["SK"]})
        last_evaluated_key = page.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    # 2. Delete the chat-index row.
    table.delete_item(Key={
        "PK": f"USER#{user_id}",
        "SK": _chat_index_sk(chat.created_at, chat.chat_id),
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_storage.py -k delete_chat -v --cov=channel.storage --cov-report=term-missing`
Expected: 2 PASS. Coverage: any new branches in `delete_chat` covered.

- [ ] **Step 5: Commit**

```bash
git add src/channel/storage.py tests/unit/test_storage.py
git commit -m "feat(channel-rd): storage.delete_chat — batched message + chat-index row delete"
```

---

## Task 4: New metric `record_chat_delete_memory_wipe_outcome`

**Files:**
- Modify: `src/channel/metrics.py`
- Modify: `tests/unit/test_metrics.py`

Counter-only EMF metric for whether the AgentCore wipe succeeded. Same shape as `record_recall_outcome` from Phase 7d — namespace `Channel`, dimension `Environment`, NO per-actor / per-chat dimensions (cardinality discipline).

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_metrics.py`, find the existing tests for `record_recall_outcome` or `record_auto_title_outcome` and append:

```python
@pytest.mark.asyncio
async def test_record_chat_delete_memory_wipe_outcome_emits_counter():
    from channel.metrics import record_chat_delete_memory_wipe_outcome

    captured = {}

    def fake_emit(payload):
        captured["payload"] = payload

    with patch("channel.metrics._emit_emf", side_effect=fake_emit):
        await record_chat_delete_memory_wipe_outcome(success=True)

    metric_payload = captured["payload"]
    cw_metrics = metric_payload["_aws"]["CloudWatchMetrics"][0]
    metric_names = [m["Name"] for m in cw_metrics["Metrics"]]
    assert metric_names == ["ChatDeleteMemoryWipeSuccesses"]
    assert metric_payload["ChatDeleteMemoryWipeSuccesses"] == 1.0


@pytest.mark.asyncio
async def test_record_chat_delete_memory_wipe_outcome_emits_failure_metric():
    from channel.metrics import record_chat_delete_memory_wipe_outcome

    captured = {}
    with patch("channel.metrics._emit_emf", side_effect=lambda p: captured.update(p=p)):
        await record_chat_delete_memory_wipe_outcome(success=False)

    cw_metrics = captured["p"]["_aws"]["CloudWatchMetrics"][0]
    metric_names = [m["Name"] for m in cw_metrics["Metrics"]]
    assert metric_names == ["ChatDeleteMemoryWipeFailures"]


def test_record_chat_delete_memory_wipe_outcome_signature_locks_out_dimensions():
    """The function MUST NOT accept actor_id / chat_id / user_id args.
    Per-actor/-chat dimensions blow up CloudWatch metric cardinality.
    """
    import inspect
    from channel.metrics import record_chat_delete_memory_wipe_outcome
    sig = inspect.signature(record_chat_delete_memory_wipe_outcome)
    param_names = set(sig.parameters)
    forbidden = {"actor_id", "chat_id", "user_id"}
    leaked = param_names & forbidden
    assert not leaked, (
        f"record_chat_delete_memory_wipe_outcome must NOT accept {leaked} "
        "— per-actor/chat dimensions cause CloudWatch cardinality blowup"
    )
```

(Adjust the exact mock target to match the existing pattern in `test_metrics.py` — the prior tests for `record_recall_outcome` show the right `patch()` target.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_metrics.py -k chat_delete -v`
Expected: 3 ImportError failures (`record_chat_delete_memory_wipe_outcome` not defined).

- [ ] **Step 3: Implement the metric**

In `src/channel/metrics.py`, find `record_recall_outcome` (the closest pattern match) and add immediately after:

```python
async def record_chat_delete_memory_wipe_outcome(*, success: bool) -> None:
    """Increment ChatDeleteMemoryWipeSuccesses or ChatDeleteMemoryWipeFailures.

    Fired from the ``DELETE /api/chats/{chat_id}`` handler in
    ``api/chats.py`` after the best-effort AgentCore Memory wipe. A
    failure means the chat is gone from DDB but the agent's recall may
    still surface its events until they age out — not user-visible
    immediately, but a real divergence worth tracking.

    Counter-only by design: no per-actor / per-chat dimensions.
    CloudWatch metric cardinality is bounded by ``Environment``.
    """
    metric_name = (
        "ChatDeleteMemoryWipeSuccesses" if success else "ChatDeleteMemoryWipeFailures"
    )
    await asyncio.to_thread(
        _emit_emf,
        {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": "Channel",
                        "Dimensions": [["Environment"]],
                        "Metrics": [{"Name": metric_name, "Unit": "Count"}],
                    },
                ],
            },
            "Environment": os.environ.get("STARTER_ENV", "unknown"),
            metric_name: 1.0,
        },
    )
```

(Adjust import / wrapper details to match the existing `record_recall_outcome` exactly — the test will tell you what shape it expects.)

- [ ] **Step 4: Run tests + coverage**

Run: `uv run pytest tests/unit/test_metrics.py --cov=channel.metrics --cov-report=term-missing`
Expected: all PASS, 100% coverage on `metrics.py`.

- [ ] **Step 5: Commit**

```bash
git add src/channel/metrics.py tests/unit/test_metrics.py
git commit -m "feat(channel-rd): record_chat_delete_memory_wipe_outcome metric"
```

---

## Task 5: Backend `DELETE /api/chats/{chat_id}` endpoint

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

Combines Task 3 (storage delete) + Task 4 (metric) + new AgentCore wipe code. Ownership check via the existing `_load_owned_chat` helper.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_chats_api.py`, append (these mirror the existing `test_patch_chat_*` test patterns; adjust fixture names to match):

```python
@pytest.mark.asyncio
async def test_delete_chat_returns_204_and_wipes_ddb(jwt_for_user, ddb_table, fake_agentcore):
    """DELETE returns 204; message rows + chat-index row gone."""
    user_id = "u-del-1"
    jwt = jwt_for_user(user_id)
    chat = storage.create_chat(user_id=user_id, title="t", model_default="claude-sonnet-4-6")

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.delete(
            f"/api/chats/{chat.chat_id}",
            headers={"Authorization": f"Bearer {jwt}"},
        )

    assert resp.status_code == 204
    assert resp.content == b""
    # GET now 404.
    async with AsyncClient(app=app, base_url="http://test") as client:
        get_resp = await client.get(
            f"/api/chats/{chat.chat_id}",
            headers={"Authorization": f"Bearer {jwt}"},
        )
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_chat_404s_on_cross_user(jwt_for_user, ddb_table):
    """A user can't delete another user's chat — returns 404, not 403,
    to avoid leaking existence."""
    chat = storage.create_chat(user_id="u-owner", title="t", model_default="claude-sonnet-4-6")
    intruder_jwt = jwt_for_user("u-intruder")

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.delete(
            f"/api/chats/{chat.chat_id}",
            headers={"Authorization": f"Bearer {intruder_jwt}"},
        )
    assert resp.status_code == 404

    # Owner can still see their chat.
    owner_jwt = jwt_for_user("u-owner")
    async with AsyncClient(app=app, base_url="http://test") as client:
        ok = await client.get(
            f"/api/chats/{chat.chat_id}",
            headers={"Authorization": f"Bearer {owner_jwt}"},
        )
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_delete_chat_calls_agentcore_delete_event_per_event(
    jwt_for_user, ddb_table, monkeypatch,
):
    """For each event returned by list_events for the chat's session,
    delete_event is called with (memoryId, actorId, sessionId, eventId)."""
    user_id = "u-del-2"
    jwt = jwt_for_user(user_id)
    chat = storage.create_chat(user_id=user_id, title="t", model_default="claude-sonnet-4-6")

    fake_client = MagicMock()
    fake_client.list_events.return_value = {
        "events": [
            {"eventId": "ev-1"},
            {"eventId": "ev-2"},
        ],
    }
    monkeypatch.setattr(
        "channel.api.chats._agentcore_client",
        lambda: fake_client,
    )
    monkeypatch.setattr(
        "channel.api.chats._memory_id_for_env",
        lambda: "channel_test-FAKEMEMID",
    )

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.delete(
            f"/api/chats/{chat.chat_id}",
            headers={"Authorization": f"Bearer {jwt}"},
        )
    assert resp.status_code == 204
    assert fake_client.list_events.called
    assert fake_client.delete_event.call_count == 2
    delete_call_kwargs = {
        call.kwargs["eventId"] for call in fake_client.delete_event.call_args_list
    }
    assert delete_call_kwargs == {"ev-1", "ev-2"}


@pytest.mark.asyncio
async def test_delete_chat_swallows_agentcore_failure(
    jwt_for_user, ddb_table, monkeypatch,
):
    """If AgentCore raises, the DDB delete still succeeds and the
    user sees 204. Metric counts the failure."""
    user_id = "u-del-3"
    jwt = jwt_for_user(user_id)
    chat = storage.create_chat(user_id=user_id, title="t", model_default="claude-sonnet-4-6")

    fake_client = MagicMock()
    fake_client.list_events.side_effect = RuntimeError("agentcore down")
    monkeypatch.setattr(
        "channel.api.chats._agentcore_client", lambda: fake_client,
    )
    monkeypatch.setattr(
        "channel.api.chats._memory_id_for_env",
        lambda: "channel_test-FAKEMEMID",
    )
    record_failure = AsyncMock()
    monkeypatch.setattr(
        "channel.api.chats.record_chat_delete_memory_wipe_outcome",
        record_failure,
    )

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.delete(
            f"/api/chats/{chat.chat_id}",
            headers={"Authorization": f"Bearer {jwt}"},
        )
    assert resp.status_code == 204  # user sees success
    record_failure.assert_awaited_once_with(success=False)
```

If the fixture / helper names differ in your test file (the codebase already has `test_patch_chat_*` tests — match their conventions), translate accordingly.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_chats_api.py -k delete_chat -v`
Expected: 4 failures — `405 Method Not Allowed` from FastAPI (no DELETE route exists yet).

- [ ] **Step 3: Implement the endpoint**

In `src/channel/api/chats.py`:

1. Add imports at the top (alongside the existing ones):
```python
import boto3
from channel.agents.memory import _sanitize_actor_id, get_or_create_memory
from channel.metrics import record_chat_delete_memory_wipe_outcome
```

2. Add helpers immediately before the existing `_load_owned_chat` (~line 90):
```python
def _agentcore_client() -> Any:
    """Lazy boto3 client construction — patched in unit tests."""
    return boto3.client("bedrock-agentcore")


def _memory_id_for_env() -> str:
    """Resolve the current env's AgentCore memoryId. Cached by
    ``get_or_create_memory``; cheap to call per request."""
    env = os.environ.get("STARTER_ENV", "unknown")
    return get_or_create_memory(env)


async def _wipe_agentcore_session(actor_id: str, chat_id: str) -> None:
    """Best-effort delete of all AgentCore events for one chat session.

    Pages list_events and calls delete_event per event. Wrapped in
    try/except at the endpoint level; this helper just does the work
    and lets exceptions propagate so the caller can record the
    failure metric. Same fail-soft contract as Phase 7c memory writes
    and Phase 8a recall.
    """
    client = _agentcore_client()
    memory_id = _memory_id_for_env()
    sanitized_actor = _sanitize_actor_id(actor_id)
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "memoryId": memory_id,
            "actorId": sanitized_actor,
            "sessionId": chat_id,
            "maxResults": 100,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        resp = await asyncio.to_thread(client.list_events, **kwargs)
        for event in resp.get("events", []):
            await asyncio.to_thread(
                client.delete_event,
                memoryId=memory_id,
                actorId=sanitized_actor,
                sessionId=chat_id,
                eventId=event["eventId"],
            )
        next_token = resp.get("nextToken")
        if not next_token:
            break
```

(Add `import asyncio` at the top if not already imported.)

3. Add the DELETE route immediately after the existing `patch_chat` (around line 137):
```python
@router.delete("/{chat_id}", status_code=204)
async def delete_chat(
    chat_id: str = Path(...),
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> Response:
    """Permanently delete a chat: DDB message rows + chat-index row +
    AgentCore session events. AgentCore failure is logged + counted
    but does NOT fail the user's delete (DDB is the source of truth
    for chat existence)."""
    chat = await _load_owned_chat(chat_id, claims["sub"])
    storage.delete_chat(user_id=claims["sub"], chat=chat)
    try:
        await _wipe_agentcore_session(actor_id=claims["sub"], chat_id=chat_id)
        await record_chat_delete_memory_wipe_outcome(success=True)
    except Exception as exc:
        logger.warning(
            "agentcore.chat_delete_wipe_failed chat_id=%s",
            chat_id,
            extra={"error_type": type(exc).__name__, "error_message": str(exc)},
            exc_info=True,
        )
        await record_chat_delete_memory_wipe_outcome(success=False)
    return Response(status_code=204)
```

- [ ] **Step 4: Run tests + coverage**

Run: `uv run pytest tests/unit/test_chats_api.py -k delete_chat --cov=channel.api.chats --cov-report=term-missing`
Expected: 4 PASS, 100% coverage on the new lines.

- [ ] **Step 5: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "feat(channel-rd): DELETE /api/chats/{chat_id} — wipes DDB + AgentCore"
```

---

## Task 6: Frontend `api.deleteChat` + `useChatList.deleteChat`

**Files:**
- Modify: `ui/src/api.js`
- Modify: `ui/src/hooks/useChatList.js`
- Modify: `ui/src/hooks/useChatList.test.js`

- [ ] **Step 1: Write the failing hook test**

In `ui/src/hooks/useChatList.test.js`, append (mirror the `archiveChat` test ~line 112):

```jsx
it("optimistically removes chat via deleteChat", async () => {
  vi.spyOn(api, "deleteChat").mockResolvedValue(undefined);
  vi.spyOn(api, "listChats").mockResolvedValue({
    items: [
      { chat_id: "a", title: "alpha", last_message_at: "2026-06-01T00:00:00Z" },
      { chat_id: "b", title: "beta", last_message_at: "2026-06-01T00:00:00Z" },
    ],
  });
  const { result } = renderHook(() => useChatList());
  await waitFor(() => expect(result.current.chats.length).toBe(2));

  await act(async () => {
    await result.current.deleteChat("a");
  });

  expect(result.current.chats.map((c) => c.chat_id)).toEqual(["b"]);
  expect(api.deleteChat).toHaveBeenCalledWith("a");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && npx vitest run src/hooks/useChatList.test.js -t "optimistically removes chat via deleteChat"`
Expected: FAIL — `api.deleteChat is not a function` and `result.current.deleteChat is undefined`.

- [ ] **Step 3: Add `deleteChat` to `ui/src/api.js`**

Add after `patchChat` (around line 154):
```js
export async function deleteChat(chatId) {
  const response = await fetch(`${BASE}/api/chats/${chatId}`, {
    method: "DELETE",
    headers: authHeader(),
  });
  if (!response.ok) throw new Error(`deleteChat ${response.status}`);
}
```

- [ ] **Step 4: Add `deleteChat` to `ui/src/hooks/useChatList.js`**

After the existing `archiveChat` definition (~line 62), add:
```js
const deleteChat = useCallback(async (chatId) => {
  setChats((prev) => prev.filter((c) => c.chat_id !== chatId));
  await api.deleteChat(chatId);
}, []);
```

And add `deleteChat` to the returned object (around line 67-75):
```js
return {
  chats,
  status,
  refresh,
  createChat,
  renameChat,
  renameChatLocal,
  archiveChat,
  deleteChat,
};
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ui && npx vitest run src/hooks/useChatList.test.js`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/src/api.js ui/src/hooks/useChatList.js ui/src/hooks/useChatList.test.js
git commit -m "feat(channel-rd): api.deleteChat + useChatList.deleteChat"
```

---

## Task 7: Re-export `deleteChat` from `ChatsContext`

**Files:**
- Modify: `ui/src/hooks/ChatsContext.jsx`
- Modify: `ui/src/hooks/ChatsContext.test.jsx`

- [ ] **Step 1: Write the failing test**

In `ui/src/hooks/ChatsContext.test.jsx`, find the existing `expect.any(Function)` assertion for `renameChat` / `archiveChat` (~line 50). Add a `deleteChat` line in the same shape:

```jsx
// In the existing "exposes the chat-lifecycle methods" test or similar:
expect(value).toMatchObject({
  // ...existing assertions...
  renameChat: expect.any(Function),
  archiveChat: expect.any(Function),
  deleteChat: expect.any(Function),
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ui && npx vitest run src/hooks/ChatsContext.test.jsx`
Expected: FAIL — `deleteChat` key not in value.

- [ ] **Step 3: Re-export in `ChatsContext.jsx`**

Find the `useChats` provider's value (probably built by destructuring `useChatList()`). Add `deleteChat` to the destructured list and to the context-value object so consumers can call `chats.deleteChat(id)`.

```jsx
// Where useChatList() is called and destructured:
const {
  chats, status, refresh, createChat, renameChat, renameChatLocal,
  archiveChat, deleteChat,  // <-- add
} = useChatList();

// In the context-value object:
const value = {
  chats, status, refresh, createChat, renameChat, renameChatLocal,
  archiveChat, deleteChat,  // <-- add
};
```

(Read the file once before editing to confirm exact structure — these are the right shapes but the variable names may differ.)

- [ ] **Step 4: Verify pass**

Run: `cd ui && npx vitest run src/hooks/ChatsContext.test.jsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/hooks/ChatsContext.jsx ui/src/hooks/ChatsContext.test.jsx
git commit -m "feat(channel-rd): expose deleteChat via ChatsContext"
```

---

## Task 8: `Modal.jsx` shared primitive

**Files:**
- Create: `ui/src/components/Modal.jsx`
- Create: `ui/src/components/Modal.test.jsx`

Backdrop + centered panel. Owns Esc-to-close and backdrop-click-to-close. Focus trap inside the panel while open. ~30 LoC.

- [ ] **Step 1: Write the failing tests**

Create `ui/src/components/Modal.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useRef } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import Modal from "./Modal.jsx";

describe("Modal", () => {
  it("renders nothing when open=false", () => {
    const { container } = render(
      <Modal open={false} onClose={() => {}}>
        <p>hidden</p>
      </Modal>,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders children when open=true", () => {
    render(
      <Modal open={true} onClose={() => {}}>
        <p>visible</p>
      </Modal>,
    );
    expect(screen.getByText("visible")).toBeTruthy();
  });

  it("calls onClose when backdrop is clicked", () => {
    const onClose = vi.fn();
    const { container } = render(
      <Modal open={true} onClose={onClose}>
        <p>x</p>
      </Modal>,
    );
    const backdrop = container.querySelector(".modal-backdrop");
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does NOT call onClose when the panel itself is clicked", () => {
    const onClose = vi.fn();
    render(
      <Modal open={true} onClose={onClose}>
        <p>panel-content</p>
      </Modal>,
    );
    fireEvent.click(screen.getByText("panel-content"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("calls onClose on Escape key", () => {
    const onClose = vi.fn();
    render(
      <Modal open={true} onClose={onClose}>
        <p>x</p>
      </Modal>,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not call onClose on Escape when closed", () => {
    const onClose = vi.fn();
    render(
      <Modal open={false} onClose={onClose}>
        <p>x</p>
      </Modal>,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ui && npx vitest run src/components/Modal.test.jsx`
Expected: import error — Modal.jsx doesn't exist.

- [ ] **Step 3: Implement `Modal.jsx`**

Create `ui/src/components/Modal.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect } from "react";

/**
 * Shared modal primitive. Backdrop + centered panel. Owns Esc-to-close
 * and backdrop-click-to-close. Renders nothing when ``open`` is false.
 *
 * Caller is responsible for the panel's content (heading, body, button
 * row). Click inside the panel does NOT close — only backdrop clicks
 * and Esc do.
 *
 * Focus management: caller is responsible for setting initial focus
 * (e.g. via ``ref`` + ``useEffect`` on the first focusable element).
 * A future iteration could add an automatic focus trap; for now the
 * simpler shape covers Rename + Delete confirms cleanly.
 */
export default function Modal({ open, onClose, children }) {
  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <>
      <div className="modal-backdrop" onClick={onClose} />
      <div className="modal" role="dialog" aria-modal="true">
        {children}
      </div>
    </>
  );
}
```

- [ ] **Step 4: Run tests to pass**

Run: `cd ui && npx vitest run src/components/Modal.test.jsx --coverage`
Expected: 6 PASS, 100% coverage on Modal.jsx.

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/Modal.jsx ui/src/components/Modal.test.jsx
git commit -m "feat(channel-rd): Modal shared primitive (backdrop + Esc + center panel)"
```

---

## Task 9: `RenameChatModal.jsx`

**Files:**
- Create: `ui/src/app/RenameChatModal.jsx`
- Create: `ui/src/app/RenameChatModal.test.jsx`

- [ ] **Step 1: Write the failing tests**

Create `ui/src/app/RenameChatModal.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import RenameChatModal from "./RenameChatModal.jsx";

// Mock useChats() so the modal can call chats.renameChat without a real provider.
const renameChatMock = vi.fn();
vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: () => ({ renameChat: renameChatMock }),
}));

describe("RenameChatModal", () => {
  beforeEach(() => {
    renameChatMock.mockReset();
    renameChatMock.mockResolvedValue(undefined);
  });

  it("renders nothing when open=false", () => {
    const { container } = render(
      <RenameChatModal open={false} chatId="c1" currentTitle="t" onClose={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("pre-fills + pre-selects the input", () => {
    render(
      <RenameChatModal open chatId="c1" currentTitle="alpha" onClose={() => {}} />,
    );
    const input = screen.getByLabelText(/title/i);
    expect(input.value).toBe("alpha");
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe("alpha".length);
  });

  it("Save is disabled when input is empty or unchanged", () => {
    render(
      <RenameChatModal open chatId="c1" currentTitle="alpha" onClose={() => {}} />,
    );
    const save = screen.getByRole("button", { name: /save/i });
    expect(save.disabled).toBe(true);  // unchanged
    const input = screen.getByLabelText(/title/i);
    fireEvent.change(input, { target: { value: "" } });
    expect(save.disabled).toBe(true);  // empty
    fireEvent.change(input, { target: { value: "beta" } });
    expect(save.disabled).toBe(false); // changed + non-empty
  });

  it("Save calls renameChat with chatId + trimmed new title and closes", async () => {
    const onClose = vi.fn();
    render(
      <RenameChatModal open chatId="c1" currentTitle="alpha" onClose={onClose} />,
    );
    const input = screen.getByLabelText(/title/i);
    fireEvent.change(input, { target: { value: "  beta  " } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(renameChatMock).toHaveBeenCalledWith("c1", "beta"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("Enter submits when valid", async () => {
    render(
      <RenameChatModal open chatId="c1" currentTitle="alpha" onClose={() => {}} />,
    );
    const input = screen.getByLabelText(/title/i);
    fireEvent.change(input, { target: { value: "beta" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(renameChatMock).toHaveBeenCalledWith("c1", "beta"));
  });

  it("Cancel button closes without calling renameChat", () => {
    const onClose = vi.fn();
    render(
      <RenameChatModal open chatId="c1" currentTitle="alpha" onClose={onClose} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(renameChatMock).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("shows inline error on API failure and stays open", async () => {
    renameChatMock.mockRejectedValueOnce(new Error("nope"));
    const onClose = vi.fn();
    render(
      <RenameChatModal open chatId="c1" currentTitle="alpha" onClose={onClose} />,
    );
    fireEvent.change(screen.getByLabelText(/title/i), { target: { value: "beta" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(screen.getByText(/couldn't rename/i)).toBeTruthy());
    expect(onClose).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ui && npx vitest run src/app/RenameChatModal.test.jsx`
Expected: import error.

- [ ] **Step 3: Implement `RenameChatModal.jsx`**

Create `ui/src/app/RenameChatModal.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef, useState } from "react";
import Modal from "../components/Modal.jsx";
import { useChats } from "../hooks/ChatsContext.jsx";

/**
 * Rename one chat. Pre-fills + pre-selects the input on mount so the
 * user can type immediately to replace the title (macOS Finder rename
 * pattern). Save is disabled when empty or unchanged. Enter submits.
 *
 * On API failure, shows an inline error line and leaves the modal
 * open so the user can retry without losing typed input. The
 * underlying useChatList.renameChat already applied an optimistic
 * local update — see useChatList.js for the revert semantics.
 */
export default function RenameChatModal({ open, chatId, currentTitle, onClose }) {
  const { renameChat } = useChats();
  const [value, setValue] = useState(currentTitle);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    setValue(currentTitle);
    setError(null);
    setBusy(false);
    // Defer focus + select until after mount so the input element exists.
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (el) {
        el.focus();
        el.select();
      }
    });
  }, [open, currentTitle]);

  const trimmed = value.trim();
  const disabled = busy || trimmed.length === 0 || trimmed === currentTitle;

  async function handleSave() {
    if (disabled) return;
    setBusy(true);
    setError(null);
    try {
      await renameChat(chatId, trimmed);
      onClose();
    } catch {
      setError("Couldn't rename the chat. Try again.");
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose}>
      <h2 className="modal-heading">Rename chat</h2>
      <label className="modal-label" htmlFor="rename-input">Title</label>
      <input
        id="rename-input"
        ref={inputRef}
        className="modal-input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !disabled) handleSave();
        }}
      />
      {error && <p className="modal-error">{error}</p>}
      <div className="modal-buttons">
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={disabled} onClick={handleSave}>
          Save
        </button>
      </div>
    </Modal>
  );
}
```

- [ ] **Step 4: Run tests + coverage**

Run: `cd ui && npx vitest run src/app/RenameChatModal.test.jsx --coverage`
Expected: 7 PASS, 100% coverage.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/RenameChatModal.jsx ui/src/app/RenameChatModal.test.jsx
git commit -m "feat(channel-rd): RenameChatModal — pre-selected input, Enter submits"
```

---

## Task 10: `DeleteChatModal.jsx`

**Files:**
- Create: `ui/src/app/DeleteChatModal.jsx`
- Create: `ui/src/app/DeleteChatModal.test.jsx`

- [ ] **Step 1: Write the failing tests**

Create `ui/src/app/DeleteChatModal.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import DeleteChatModal from "./DeleteChatModal.jsx";

const deleteChatMock = vi.fn();
const navigateMock = vi.fn();

vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: () => ({ deleteChat: deleteChatMock }),
}));
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigateMock };
});

function renderModal(props) {
  return render(
    <MemoryRouter>
      <DeleteChatModal {...props} />
    </MemoryRouter>,
  );
}

describe("DeleteChatModal", () => {
  beforeEach(() => {
    deleteChatMock.mockReset();
    deleteChatMock.mockResolvedValue(undefined);
    navigateMock.mockReset();
  });

  it("renders nothing when open=false", () => {
    const { container } = renderModal({
      open: false, chatId: "c1", chatTitle: "alpha", isActive: false, onClose: () => {},
    });
    expect(container.firstChild).toBeNull();
  });

  it("renders title + warning text + danger-styled Delete button", () => {
    renderModal({ open: true, chatId: "c1", chatTitle: "alpha", isActive: false, onClose: () => {} });
    expect(screen.getByText(/delete chat\?/i)).toBeTruthy();
    expect(screen.getByText(/alpha/)).toBeTruthy();
    expect(screen.getByText(/cannot be undone/i)).toBeTruthy();
    const del = screen.getByRole("button", { name: /^delete$/i });
    expect(del.classList.contains("btn-danger")).toBe(true);
  });

  it("Cancel closes without deleting", () => {
    const onClose = vi.fn();
    renderModal({ open: true, chatId: "c1", chatTitle: "a", isActive: false, onClose });
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(deleteChatMock).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("Delete calls deleteChat(chatId) and closes", async () => {
    const onClose = vi.fn();
    renderModal({ open: true, chatId: "c1", chatTitle: "a", isActive: false, onClose });
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(deleteChatMock).toHaveBeenCalledWith("c1"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(navigateMock).not.toHaveBeenCalled();  // not the active chat
  });

  it("navigates to /app BEFORE deleting when isActive=true", async () => {
    renderModal({ open: true, chatId: "c1", chatTitle: "a", isActive: true, onClose: () => {} });
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/app"));
    await waitFor(() => expect(deleteChatMock).toHaveBeenCalledWith("c1"));
    // Order: navigate first, then delete.
    expect(navigateMock.mock.invocationCallOrder[0])
      .toBeLessThan(deleteChatMock.mock.invocationCallOrder[0]);
  });

  it("default focus is on Cancel, not Delete", () => {
    renderModal({ open: true, chatId: "c1", chatTitle: "a", isActive: false, onClose: () => {} });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /cancel/i }));
  });
});
```

- [ ] **Step 2: Verify failure**

Run: `cd ui && npx vitest run src/app/DeleteChatModal.test.jsx`
Expected: import error.

- [ ] **Step 3: Implement `DeleteChatModal.jsx`**

Create `ui/src/app/DeleteChatModal.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import Modal from "../components/Modal.jsx";
import { useChats } from "../hooks/ChatsContext.jsx";

/**
 * Confirm-and-delete one chat. Permanently removes the chat from both
 * DynamoDB (message rows + chat-index row) and AgentCore Memory (all
 * events for the chat's session). Delete is irreversible by design.
 *
 * Default focus is on Cancel so a stray Enter dismisses; user must
 * Tab to Delete + Enter (or click) to confirm. macOS convention for
 * destructive defaults.
 *
 * If ``isActive`` is true (the user is currently viewing this chat),
 * navigate to ``/app`` BEFORE invoking the delete so the user never
 * sees Conversation.jsx flash a 404 state during the optimistic local
 * removal.
 */
export default function DeleteChatModal({ open, chatId, chatTitle, isActive, onClose }) {
  const { deleteChat } = useChats();
  const navigate = useNavigate();
  const cancelRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => cancelRef.current?.focus());
  }, [open]);

  async function handleConfirm() {
    if (isActive) navigate("/app");
    try {
      await deleteChat(chatId);
    } finally {
      onClose();
    }
  }

  return (
    <Modal open={open} onClose={onClose}>
      <h2 className="modal-heading">Delete chat?</h2>
      <p className="modal-body">
        This will permanently delete &ldquo;{chatTitle}&rdquo; and remove it from
        the AI&rsquo;s memory. This action cannot be undone.
      </p>
      <div className="modal-buttons">
        <button
          type="button"
          ref={cancelRef}
          className="btn-secondary"
          onClick={onClose}
        >
          Cancel
        </button>
        <button type="button" className="btn-danger" onClick={handleConfirm}>
          Delete
        </button>
      </div>
    </Modal>
  );
}
```

- [ ] **Step 4: Verify pass**

Run: `cd ui && npx vitest run src/app/DeleteChatModal.test.jsx --coverage`
Expected: 6 PASS, 100% coverage.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/DeleteChatModal.jsx ui/src/app/DeleteChatModal.test.jsx
git commit -m "feat(channel-rd): DeleteChatModal — confirm + danger button + active-chat redirect"
```

---

## Task 11: `ChatRowMenu.jsx` popover

**Files:**
- Create: `ui/src/app/ChatRowMenu.jsx`
- Create: `ui/src/app/ChatRowMenu.test.jsx`

5-item popover. Pin / Change project / Remove from project are disabled placeholders. Rename + Delete fire callbacks. Backdrop click + Esc dismiss.

- [ ] **Step 1: Write the failing tests**

Create `ui/src/app/ChatRowMenu.test.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import ChatRowMenu from "./ChatRowMenu.jsx";

const FAKE_RECT = { top: 100, left: 50, bottom: 130, right: 250, width: 200, height: 30 };

function renderMenu(overrides = {}) {
  const props = {
    open: true,
    anchorRect: FAKE_RECT,
    onClose: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    ...overrides,
  };
  const utils = render(<ChatRowMenu {...props} />);
  return { ...utils, props };
}

describe("ChatRowMenu", () => {
  it("renders nothing when open=false", () => {
    const { container } = render(
      <ChatRowMenu open={false} anchorRect={FAKE_RECT}
        onClose={() => {}} onRename={() => {}} onDelete={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders all 5 menu items in spec order", () => {
    renderMenu();
    const items = screen.getAllByRole("menuitem");
    expect(items.map((el) => el.textContent.trim())).toEqual([
      "Pin",
      "Rename",
      "Change project",
      "Remove from project",
      "Delete",
    ]);
  });

  it("Pin / Change project / Remove from project are disabled placeholders", () => {
    renderMenu();
    expect(screen.getByRole("menuitem", { name: /^pin$/i }).getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("menuitem", { name: /change project/i }).getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("menuitem", { name: /remove from project/i }).getAttribute("aria-disabled")).toBe("true");
  });

  it("Rename and Delete are NOT disabled", () => {
    renderMenu();
    expect(screen.getByRole("menuitem", { name: /^rename$/i }).getAttribute("aria-disabled")).not.toBe("true");
    expect(screen.getByRole("menuitem", { name: /^delete$/i }).getAttribute("aria-disabled")).not.toBe("true");
  });

  it("Delete item is danger-styled", () => {
    renderMenu();
    const del = screen.getByRole("menuitem", { name: /^delete$/i });
    expect(del.classList.contains("danger")).toBe(true);
  });

  it("clicking Rename calls onRename + onClose", () => {
    const { props } = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /rename/i }));
    expect(props.onRename).toHaveBeenCalled();
    expect(props.onClose).toHaveBeenCalled();
  });

  it("clicking Delete calls onDelete + onClose", () => {
    const { props } = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
    expect(props.onDelete).toHaveBeenCalled();
    expect(props.onClose).toHaveBeenCalled();
  });

  it("clicking a disabled item is a no-op", () => {
    const { props } = renderMenu();
    fireEvent.click(screen.getByRole("menuitem", { name: /^pin$/i }));
    expect(props.onRename).not.toHaveBeenCalled();
    expect(props.onDelete).not.toHaveBeenCalled();
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("backdrop click closes", () => {
    const { container, props } = renderMenu();
    fireEvent.click(container.querySelector(".backdrop"));
    expect(props.onClose).toHaveBeenCalled();
  });

  it("Escape closes", () => {
    const { props } = renderMenu();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(props.onClose).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Verify failure**

Run: `cd ui && npx vitest run src/app/ChatRowMenu.test.jsx`
Expected: import error.

- [ ] **Step 3: Implement `ChatRowMenu.jsx`**

Create `ui/src/app/ChatRowMenu.jsx`:

```jsx
// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect } from "react";
import Icon from "../components/Icon.jsx";

/**
 * Per-row popover anchored to a sidebar Recents chat. 5 items
 * matching the reference UX; Pin / Change project / Remove from
 * project are disabled placeholders (rendered for menu shape; future
 * features wire them).
 *
 * Positioning: ``anchorRect`` is the bounding rect of the row that
 * spawned the menu. The popover anchors to the row's right edge and
 * opens downward; if the row is near the viewport bottom, it flips
 * to opening upward instead.
 *
 * Backdrop click + Esc both close. Same pattern as ModelPicker /
 * AttachMenu / AccountPopover per CLAUDE.md §UI conventions.
 */
export default function ChatRowMenu({ open, anchorRect, onClose, onRename, onDelete }) {
  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || !anchorRect) return null;

  const MENU_HEIGHT_ESTIMATE = 220;
  const flipUp = anchorRect.bottom + MENU_HEIGHT_ESTIMATE > window.innerHeight;
  const style = {
    position: "fixed",
    left: anchorRect.right - 8,  // align right edge of menu near right edge of row
    top: flipUp ? undefined : anchorRect.bottom + 4,
    bottom: flipUp ? window.innerHeight - anchorRect.top + 4 : undefined,
  };

  function handleClick(callback) {
    return () => {
      callback();
      onClose();
    };
  }

  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="pop chat-row-menu" style={style} role="menu">
        <button
          type="button"
          role="menuitem"
          className="chat-row-menu-item disabled"
          aria-disabled="true"
          title="Coming soon"
        >
          <Icon name="pin" size={16} /> <span>Pin</span>
        </button>
        <button
          type="button"
          role="menuitem"
          className="chat-row-menu-item"
          onClick={handleClick(onRename)}
        >
          <Icon name="pencil" size={16} /> <span>Rename</span>
        </button>
        <button
          type="button"
          role="menuitem"
          className="chat-row-menu-item disabled"
          aria-disabled="true"
          title="Coming soon"
        >
          <Icon name="projects" size={16} /> <span>Change project</span>
        </button>
        <button
          type="button"
          role="menuitem"
          className="chat-row-menu-item disabled"
          aria-disabled="true"
          title="Coming soon"
        >
          <Icon name="folder-x" size={16} /> <span>Remove from project</span>
        </button>
        <button
          type="button"
          role="menuitem"
          className="chat-row-menu-item danger"
          onClick={handleClick(onDelete)}
        >
          <Icon name="trash" size={16} /> <span>Delete</span>
        </button>
      </div>
    </>
  );
}
```

- [ ] **Step 4: Verify pass**

Run: `cd ui && npx vitest run src/app/ChatRowMenu.test.jsx --coverage`
Expected: 10 PASS, 100% coverage.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/ChatRowMenu.jsx ui/src/app/ChatRowMenu.test.jsx
git commit -m "feat(channel-rd): ChatRowMenu — 5-item popover (3 disabled placeholders)"
```

---

## Task 12: Wire it all into `Sidebar.jsx`

**Files:**
- Modify: `ui/src/app/Sidebar.jsx`
- Modify: `ui/src/app/Sidebar.test.jsx`

Per-row more-vertical button + menu state + modal hosting.

- [ ] **Step 1: Write the failing tests**

In `ui/src/app/Sidebar.test.jsx`, append (alongside existing sidebar tests; mock useChats to provide the lifecycle methods):

```jsx
// At the top of the file, add a mock for useChats if not already present.
vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: () => ({
    renameChat: vi.fn(),
    archiveChat: vi.fn(),
    deleteChat: vi.fn(),
    renameChatLocal: vi.fn(),
  }),
}));

describe("Sidebar — per-row menu", () => {
  function renderSidebarWithChat(chat) {
    return render(
      <MemoryRouter>
        <Sidebar chats={[chat]} />
      </MemoryRouter>,
    );
  }

  it("renders a more-vertical button per chat row", () => {
    renderSidebarWithChat({
      chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString(),
    });
    expect(screen.getByLabelText(/more options for alpha/i)).toBeTruthy();
  });

  it("clicking the menu button opens ChatRowMenu", () => {
    renderSidebarWithChat({
      chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString(),
    });
    fireEvent.click(screen.getByLabelText(/more options for alpha/i));
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /rename/i })).toBeTruthy();
  });

  it("clicking Rename opens RenameChatModal", () => {
    renderSidebarWithChat({
      chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString(),
    });
    fireEvent.click(screen.getByLabelText(/more options for alpha/i));
    fireEvent.click(screen.getByRole("menuitem", { name: /rename/i }));
    expect(screen.getByText(/rename chat/i)).toBeTruthy();
    expect(screen.getByLabelText(/title/i).value).toBe("alpha");
  });

  it("clicking Delete opens DeleteChatModal", () => {
    renderSidebarWithChat({
      chat_id: "c1", title: "alpha", last_message_at: new Date().toISOString(),
    });
    fireEvent.click(screen.getByLabelText(/more options for alpha/i));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
    expect(screen.getByText(/delete chat\?/i)).toBeTruthy();
  });
});
```

(Adjust imports to include `screen`, `fireEvent`, `MemoryRouter` if not already imported.)

- [ ] **Step 2: Verify failure**

Run: `cd ui && npx vitest run src/app/Sidebar.test.jsx -t "per-row menu"`
Expected: 4 failures — menu button not present.

- [ ] **Step 3: Modify `Sidebar.jsx`**

Add imports at the top:
```jsx
import ChatRowMenu from "./ChatRowMenu.jsx";
import RenameChatModal from "./RenameChatModal.jsx";
import DeleteChatModal from "./DeleteChatModal.jsx";
import { useParams } from "react-router-dom";
```

Inside the component, after the existing state declarations (around line 56):
```jsx
const [menuFor, setMenuFor] = useState(null);  // { chatId, anchorRect } | null
const [renameFor, setRenameFor] = useState(null);  // { chatId, currentTitle } | null
const [deleteFor, setDeleteFor] = useState(null);  // { chatId, chatTitle } | null
const params = useParams();
const activeChatId = params.chatId ?? null;
```

Replace the chat-row mapping inside the existing `{groups.map(...)` block (around line 165-178):

```jsx
{groups.map((g) => (
  <div key={g.name}>
    <div className="sb-section">{g.name}</div>
    {g.items.map((c) => (
      <div key={c.chat_id} className="recent-wrap">
        <button
          type="button"
          className="recent"
          onClick={() => navigate(`/app/c/${c.chat_id}`)}
        >
          {c.title}
        </button>
        <button
          type="button"
          className="recent-menu-btn"
          aria-label={`More options for ${c.title}`}
          onClick={(e) => {
            e.stopPropagation();
            const rect = e.currentTarget
              .closest(".recent-wrap")
              .getBoundingClientRect();
            setMenuFor({ chatId: c.chat_id, chatTitle: c.title, anchorRect: rect });
          }}
        >
          <Icon name="more-vertical" size={16} />
        </button>
      </div>
    ))}
  </div>
))}
```

At the end of the component's return, immediately before the closing `</div>` of the outer `<div className="sb">`, mount the menu + modals:

```jsx
{menuFor && (
  <ChatRowMenu
    open
    anchorRect={menuFor.anchorRect}
    onClose={() => setMenuFor(null)}
    onRename={() => {
      setRenameFor({ chatId: menuFor.chatId, currentTitle: menuFor.chatTitle });
    }}
    onDelete={() => {
      setDeleteFor({ chatId: menuFor.chatId, chatTitle: menuFor.chatTitle });
    }}
  />
)}
{renameFor && (
  <RenameChatModal
    open
    chatId={renameFor.chatId}
    currentTitle={renameFor.currentTitle}
    onClose={() => setRenameFor(null)}
  />
)}
{deleteFor && (
  <DeleteChatModal
    open
    chatId={deleteFor.chatId}
    chatTitle={deleteFor.chatTitle}
    isActive={activeChatId === deleteFor.chatId}
    onClose={() => setDeleteFor(null)}
  />
)}
```

- [ ] **Step 4: Verify pass**

Run: `cd ui && npx vitest run src/app/Sidebar.test.jsx`
Expected: all PASS, including pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add ui/src/app/Sidebar.jsx ui/src/app/Sidebar.test.jsx
git commit -m "feat(channel-rd): wire menu button + modals into Sidebar"
```

---

## Task 13: CSS — menu button hover, popover, modal, danger button

**Files:**
- Modify: `ui/src/styles/app.css`

All styling lands in one commit so the components above go from functional to polished in one step.

- [ ] **Step 1: Append CSS rules**

Append to the end of `ui/src/styles/app.css`:

```css
/* ----- Chat row hover menu ----------------------------------------- */
.recent-wrap {
  position: relative;
  display: flex;
  align-items: center;
}
.recent-wrap .recent {
  flex: 1;
  padding-right: 30px;  /* room for the menu button */
}
.recent-menu-btn {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 0;
  border-radius: 6px;
  color: var(--ink-faint);
  opacity: 0;
  transition: opacity .12s ease, background-color .12s ease;
  cursor: pointer;
}
.recent-wrap:hover .recent-menu-btn,
.recent-menu-btn:focus-visible {
  opacity: 1;
}
.recent-menu-btn:hover {
  background: var(--raised);
  color: var(--ink);
}

/* ----- ChatRowMenu popover ----------------------------------------- */
.chat-row-menu {
  min-width: 220px;
  padding: 4px;
  z-index: 60;
}
.chat-row-menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 7px 10px;
  background: transparent;
  border: 0;
  border-radius: 6px;
  text-align: left;
  font-size: 13.5px;
  color: var(--ink);
  cursor: pointer;
}
.chat-row-menu-item:hover:not(.disabled) {
  background: var(--raised);
}
.chat-row-menu-item.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.chat-row-menu-item.danger {
  color: var(--danger);
}
.chat-row-menu-item.danger:hover {
  background: var(--danger-soft);
}

/* ----- Modal ------------------------------------------------------- */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 90;
}
.modal {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  background: var(--canvas);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 22px 22px 18px;
  min-width: 360px;
  max-width: 480px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
  z-index: 91;
}
.modal-heading {
  margin: 0 0 12px;
  font-size: 17px;
  font-weight: 600;
  color: var(--ink);
}
.modal-label {
  display: block;
  font-size: 12px;
  color: var(--ink-faint);
  margin: 0 0 6px;
}
.modal-input {
  width: 100%;
  padding: 8px 10px;
  background: var(--canvas);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--ink);
  font-size: 14px;
  outline: none;
}
.modal-input:focus {
  border-color: var(--accent);
}
.modal-body {
  margin: 0 0 16px;
  font-size: 14px;
  line-height: 1.5;
  color: var(--ink-soft);
}
.modal-error {
  margin: 8px 0 0;
  font-size: 12.5px;
  color: var(--danger);
}
.modal-buttons {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
.btn-primary,
.btn-secondary,
.btn-danger {
  padding: 7px 14px;
  border-radius: 8px;
  font-size: 13.5px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent;
}
.btn-primary {
  background: var(--accent);
  color: white;
}
.btn-primary:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.btn-secondary {
  background: transparent;
  color: var(--ink);
  border-color: var(--border);
}
.btn-secondary:hover {
  background: var(--raised);
}
.btn-danger {
  background: var(--danger);
  color: white;
}
.btn-danger:hover {
  filter: brightness(1.08);
}
```

- [ ] **Step 2: Smoke-build the UI**

Run: `cd ui && npx vite build --mode development 2>&1 | tail -5`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add ui/src/styles/app.css
git commit -m "style(channel-rd): hover menu button, popover, modal, danger button"
```

---

## Task 14: Playwright e2e

**Files:**
- Create: `tests/e2e/test_chat_management.py`

Exercises rename + delete end-to-end against a personal AWS env via `inv e2e-local`.

- [ ] **Step 1: Create the test file**

```python
# Copyright (c) 2026 John Carter. All rights reserved.
"""End-to-end verification of chat rename + delete from the sidebar.

Drives the UI through:
1. Create a chat (send one message)
2. Hover the row → assert menu button is visible
3. Click menu → assert 5 items render (3 disabled, 2 enabled)
4. Click Rename → modal opens with pre-filled input → submit → assert
   sidebar updated AND ``GET /api/chats/{id}`` returns the new title
5. Click Delete → confirm modal → assert row gone AND
   ``GET /api/chats/{id}`` returns 404 AND AgentCore session has no
   events
"""

from __future__ import annotations

import asyncio
import html as html_lib
import os
import re
import time
import uuid

import httpx
import pytest
from playwright.async_api import async_playwright

pytestmark = pytest.mark.asyncio


@pytest.mark.asyncio
async def test_rename_and_delete_chat_from_sidebar() -> None:
    ui_url = os.environ.get("STARTER_UI_URL")
    api_url = os.environ.get("STARTER_API_URL", "http://localhost:8001")
    if not ui_url:
        pytest.skip("STARTER_UI_URL not set — run via `inv e2e-local`")

    tag = f"e2e-rd-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    email = f"{tag}@example.com"
    jwt = _mint_jwt_via_bypass(api_url, email)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(f"{ui_url}/app")
            await page.evaluate(
                "(t) => window.localStorage.setItem('starter_mgmt_token', t)",
                jwt,
            )
            await page.goto(f"{ui_url}/app")
            await page.wait_for_url(lambda url: "/app" in url, timeout=10_000)

            # Send a message to create + populate the chat.
            await _send_one_message(page, "test chat for rename/delete")
            await page.wait_for_url(lambda url: "/app/c/" in url, timeout=15_000)
            chat_id = page.url.rsplit("/", 1)[-1]
            await _wait_for_assistant_idle(page, n=1, timeout_ms=90_000)
            # The auto-titler will rename it shortly; for rename test we override.

            # Hover the row → assert menu button becomes visible.
            row = page.locator(".recent-wrap").first
            await row.hover()
            menu_btn = row.locator(".recent-menu-btn")
            await menu_btn.wait_for(state="visible")
            await menu_btn.click()

            # 5 items render.
            menu_items = page.locator('[role="menuitem"]')
            assert await menu_items.count() == 5

            # Rename flow.
            await page.locator('[role="menuitem"]', has_text="Rename").click()
            input_el = page.locator("#rename-input")
            await input_el.wait_for(state="visible")
            await input_el.fill("Renamed via e2e")
            await page.locator('button:has-text("Save")').click()
            await page.wait_for_function(
                "() => !!document.querySelector('.recent')"
                " && Array.from(document.querySelectorAll('.recent'))"
                ".some(el => el.textContent.includes('Renamed via e2e'))",
                timeout=8_000,
            )
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{api_url}/api/chats/{chat_id}",
                    headers={"Authorization": f"Bearer {jwt}"},
                )
            assert resp.status_code == 200
            assert resp.json()["chat"]["title"] == "Renamed via e2e"

            # Delete flow.
            await row.hover()
            await menu_btn.click()
            await page.locator('[role="menuitem"]', has_text="Delete").click()
            await page.locator(".modal", has_text="Delete chat?").wait_for(state="visible")
            await page.locator('button.btn-danger:has-text("Delete")').click()
            # Row gone from sidebar.
            await page.wait_for_function(
                "() => !Array.from(document.querySelectorAll('.recent'))"
                ".some(el => el.textContent.includes('Renamed via e2e'))",
                timeout=8_000,
            )
            # Chat gone from API.
            async with httpx.AsyncClient(timeout=10.0) as client:
                gone = await client.get(
                    f"{api_url}/api/chats/{chat_id}",
                    headers={"Authorization": f"Bearer {jwt}"},
                )
            assert gone.status_code == 404

            # AgentCore session has no events (best-effort wipe).
            # Brief window for AgentCore eventual consistency.
            await asyncio.sleep(3)
            async with httpx.AsyncClient(timeout=10.0) as client:
                events_resp = await client.get(
                    f"{api_url}/api/_debug/memory/events",
                    params={"chat_id": chat_id, "limit": 10},
                    headers={"Authorization": f"Bearer {jwt}"},
                )
            # If debug endpoints aren't enabled, skip the structural check.
            if events_resp.status_code == 200:
                assert events_resp.json()["events"] == []
        finally:
            await browser.close()


# Helpers — same shape as other Playwright tests in this directory.


def _mint_jwt_via_bypass(api_url: str, email: str) -> str:
    resp = httpx.get(
        f"{api_url}/auth/login",
        params={"test_email": email},
        follow_redirects=False,
        timeout=15.0,
    )
    if resp.status_code in (301, 302, 307, 308):
        pytest.skip("Google OAuth redirect — STARTER_BYPASS_GOOGLE_AUTH not enabled")
    resp.raise_for_status()
    m = re.search(
        r"localStorage\.setItem\('starter_mgmt_token',\s*'([^']+)'\)",
        resp.text,
    )
    if not m:
        pytest.fail("Could not extract mgmt token from bypass login response")
    return html_lib.unescape(m.group(1))


async def _send_one_message(page, text: str) -> None:
    textarea = page.locator("textarea").first
    await textarea.fill(text)
    await textarea.press("Enter")


async def _wait_for_assistant_idle(page, *, n: int, timeout_ms: int) -> None:
    await page.wait_for_function(
        "(want) => document.querySelectorAll('[data-testid=\"assistant-turn-idle\"]').length >= want",
        arg=n,
        timeout=timeout_ms,
    )
```

- [ ] **Step 2: Verify collection**

Run: `uv run pytest tests/e2e/test_chat_management.py --collect-only`
Expected: 1 test collected, no errors.

- [ ] **Step 3: Run against personal AWS env**

Make sure `inv dev` is running, then:

```bash
uv run inv e2e-local --tests tests/e2e/test_chat_management.py -n 3
```

Expected: 3/3 PASS. If the auto-titler renames the chat away from "test chat for rename/delete" before the rename click, that's fine — the rename test sets a deterministic title, then asserts it.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_chat_management.py
git commit -m "test(channel-rd): e2e — rename + delete from sidebar menu"
```

---

## Task 15: CHANGELOG + CLAUDE.md sync

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: CHANGELOG entry**

Open `CHANGELOG.md`. Add to `[Unreleased]` `### Added`:

```markdown
- Chat rename + delete from the sidebar. Hovering a chat in the
  Recents list reveals a `⋮` menu button; clicking it opens a popover
  with Rename (wired), Delete (wired, permanent), and three disabled
  placeholders (Pin, Change project, Remove from project). Delete
  wipes the chat from both DynamoDB AND AgentCore Memory so the
  agent stops recalling the deleted conversation in future chats.
  Introduces a reusable `Modal.jsx` primitive and a `--danger` CSS
  token (no destructive colour existed yet).
```

- [ ] **Step 2: CLAUDE.md updates**

In `CLAUDE.md`:

(a) Under `## Structure` in the `ui/src/components/` block, add `Modal.jsx` and bump `Icon.jsx`'s description if needed.
```
│   │   │   ├── Modal.jsx          # Shared modal primitive (Esc + backdrop dismissal)
```

(b) Under `## Structure` in the `ui/src/app/` block, add:
```
│   │   │   ├── ChatRowMenu.jsx          # Per-row sidebar popover (rename/delete/placeholders)
│   │   │   ├── RenameChatModal.jsx      # Rename chat dialog
│   │   │   ├── DeleteChatModal.jsx      # Delete confirm dialog
```

(c) Under `## UI conventions`, add a line:
```
- **Modal dialogs** — all destructive confirms and edit-in-place dialogs
  use `Modal.jsx` (`ui/src/components/Modal.jsx`) for backdrop + Esc
  dismissal. Don't reinvent the modal shell.
```

(d) Under `## AgentCore Memory` (the parent block, not the `### Recall` subsection), add this note near the Failure mode bullet:

```
- **Chat deletion wipes session events** — `DELETE /api/chats/{chat_id}`
  removes the chat from DynamoDB AND best-effort deletes the
  AgentCore Memory events for the chat's session (`ListEvents` +
  `DeleteEvent`). Failure is logged + counted (`ChatDeleteMemoryWipeFailures`)
  but does not fail the user-visible delete — DDB is the source of
  truth for chat existence.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs(channel-rd): CHANGELOG + CLAUDE.md sync — chat rename + delete"
```

---

## Task 16: Pre-push + issue + PR

- [ ] **Step 1: Pre-push gate**

```bash
uv run inv pre-push
```

Expected: `All checks passed!`.

If the gate fails on ruff format (the project's recurring CI hiccup), run `uv run ruff format src tests` and re-commit as a `style(channel-rd): ruff format` follow-up commit.

- [ ] **Step 2: File the issue**

```bash
gh issue create \
  --title "feat(channel-rd): chat rename + delete from sidebar" \
  --label "status:ready,priority:p1,size:m,enhancement,agent-safe" \
  --body "$(cat <<'EOF'
Adds user-facing rename + delete actions to each chat in the sidebar.
Hover-revealed ``⋮`` button → popover with Pin (disabled), Rename
(wired), Change project (disabled), Remove from project (disabled),
Delete (wired, permanent). Delete wipes both DDB rows and AgentCore
Memory events.

Design: docs/superpowers/specs/2026-06-01-chat-rename-delete-design.md
Plan: docs/superpowers/plans/2026-06-01-chat-rename-delete.md

## Files to touch

- src/channel/api/chats.py
- src/channel/storage.py
- src/channel/metrics.py
- ui/src/api.js
- ui/src/hooks/useChatList.js
- ui/src/hooks/ChatsContext.jsx
- ui/src/components/Modal.jsx
- ui/src/components/Icon.jsx
- ui/src/app/Sidebar.jsx
- ui/src/app/ChatRowMenu.jsx
- ui/src/app/RenameChatModal.jsx
- ui/src/app/DeleteChatModal.jsx
- ui/src/styles/channel.css
- ui/src/styles/app.css
- tests/unit/test_storage.py
- tests/unit/test_chats_api.py
- tests/unit/test_metrics.py
- tests/e2e/test_chat_management.py
- CHANGELOG.md
- CLAUDE.md
- docs/superpowers/specs/2026-06-01-chat-rename-delete-design.md
- docs/superpowers/plans/2026-06-01-chat-rename-delete.md
EOF
)"
```

- [ ] **Step 3: Push + open PR**

```bash
git fetch origin
git rebase origin/development
git log --oneline origin/development..HEAD  # confirm only your commits

git push -u origin feat/chat-rename-delete:feat/chat-rename-delete

gh pr create --base development \
  --title "feat(channel-rd): chat rename + delete from sidebar" \
  --body "$(cat <<'EOF'
## Summary

- Hover-revealed ``⋮`` menu per chat row in the sidebar. Click opens a popover with 5 items: Pin (disabled placeholder), Rename (wired), Change project (disabled placeholder), Remove from project (disabled placeholder), Delete (wired, permanent, danger-styled).
- Rename opens a modal with pre-filled, pre-selected title input. Enter submits, Esc/backdrop cancels. On API failure the modal stays open with an inline error.
- Delete opens a confirm modal with the chat title quoted in the body. Default focus is Cancel; a Tab + Enter or click on Delete confirms. If the deleted chat is the one currently being viewed, the modal navigates the user to ``/app`` before invoking the delete to prevent a 404 flash.
- Backend: new ``DELETE /api/chats/{chat_id}`` removes message rows + chat-index row from DDB, then best-effort deletes the AgentCore Memory events for the chat's session. AgentCore failure is logged + counted (``ChatDeleteMemoryWipeFailures``) but does NOT fail the user's delete — DDB is the source of truth for chat existence.
- New reusable ``Modal.jsx`` primitive (backdrop + Esc + centered panel). New ``--danger`` CSS token (none existed before — the codebase had no destructive-action colour).

Closes #<NNN-from-step-2>

## Test plan

- [x] Layer 1 — ``inv pre-push`` green. 100% coverage on new files (``Modal.jsx``, ``ChatRowMenu.jsx``, ``RenameChatModal.jsx``, ``DeleteChatModal.jsx``, ``delete_chat`` storage helper, DELETE endpoint, new metric).
- [x] Layer 2 — Playwright e2e ``tests/e2e/test_chat_management.py`` passes 3 consecutive runs against personal AWS env: row hover reveals menu button → menu renders 5 items → Rename updates title in sidebar + DDB → Delete removes row from sidebar AND returns 404 from API AND AgentCore session has no events.
- [ ] Layer 3 — after merge + dev deploy: rename and delete a chat from https://channel-dev.warlordofmars.net/app . Verify the chat disappears from the sidebar AND in a new chat, asking the agent about the deleted conversation surfaces no context (recall doesn't return the deleted session's events).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"

gh pr merge --auto --squash --delete-branch
```

- [ ] **Step 4: Watch CI**

```bash
gh run watch
```

If CI fails on the agent-safe scope check because the issue body's `## Files to touch` doesn't list a file the PR touched, edit the issue to add it and re-run the scope-check workflow (`gh workflow run "agent-safe-scope.yml" --ref feat/chat-rename-delete -f pr_number=<n>`).

- [ ] **Step 5: Layer 3 verification (after dev deploy)**

After auto-merge + dev deploy:
1. Open `https://channel-dev.warlordofmars.net/app`.
2. Send a message to create a chat (e.g. "Layer 3 test rename and delete").
3. Hover the row → click `⋮` → click Rename → enter "Layer 3 verified" → Save. Confirm the sidebar updates.
4. Hover again → `⋮` → Delete → confirm. Sidebar row vanishes.
5. Start a new chat and ask "what was the title of my last test chat?" — the agent should NOT mention "Layer 3 verified" (recall context wiped).

---

## Done criteria

- ✅ Hover-revealed `⋮` menu button on every `.recent` row.
- ✅ Popover renders Pin / Rename / Change project / Remove from project / Delete; only Rename and Delete are wired.
- ✅ Rename modal pre-fills + pre-selects input, validates non-empty + changed, persists via `PATCH /api/chats/{id}`.
- ✅ Delete confirm modal styled-as-danger, focus defaults to Cancel, navigates away if the active chat is being deleted, then permanently wipes DDB + AgentCore.
- ✅ 100% coverage on new files; full `inv pre-push` green.
- ✅ Playwright e2e passes 3 consecutive runs.
- ✅ CHANGELOG + CLAUDE.md updated.
- ✅ Live verification on dev: rename + delete work end-to-end and the agent doesn't recall the deleted chat.
