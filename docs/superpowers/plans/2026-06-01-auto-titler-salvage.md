# Auto-titler salvage — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Salvage partial titler output when Strands raises `MaxTokensReachedException`, instead of discarding it and leaving the chat stuck on "New chat".

**Architecture:** Catch `MaxTokensReachedException` specifically (inside an inner try/except inside the existing outer one), post-process the already-accumulated `title_chunks` via a new `_postprocess_title` helper (strips colon-prefixed preamble, caps at 6 words), and persist the salvaged title if non-empty. The outer `except Exception` keeps the existing fail-soft pattern for everything else.

**Tech Stack:** FastAPI streaming, Strands `Agent`, `strands.types.exceptions.MaxTokensReachedException`.

**Spec:** `docs/superpowers/specs/2026-06-01-auto-titler-salvage-design.md` — read it before starting Task 1. Empty-preamble → empty title → failure outcome is a spec decision; don't reinvent.

---

## File map

**Modify:**
- `src/channel/api/chats.py` — add `_postprocess_title` helper + restructure titler block in `_stream_bedrock_reply`.
- `tests/unit/test_chats_api.py` — six new tests (pure helper + streaming behavior).
- `CHANGELOG.md` — one bullet under `[Unreleased]` `### Fixed`.

**Create:** none.

---

## Task 1: `_postprocess_title` helper (pure function, TDD)

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

The helper is pure — string in, string out, no I/O — so write it test-first and lock the contract before touching the streaming block.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_chats_api.py`, append a new test section. Match the file's existing import + test style (read the file once to confirm — most likely `from channel.api.chats import _postprocess_title` near the top is fine):

```python
# ---- _postprocess_title helper ----------------------------------------------

def test_postprocess_title_passes_clean_input_through():
    from channel.api.chats import _postprocess_title
    assert _postprocess_title("Debug pytest fixture") == "Debug pytest fixture"


def test_postprocess_title_strips_colon_prefixed_preamble():
    from channel.api.chats import _postprocess_title
    assert _postprocess_title(
        "Here's a 3-6 word title: Debug pytest fixture",
    ) == "Debug pytest fixture"


def test_postprocess_title_returns_empty_on_empty_input():
    from channel.api.chats import _postprocess_title
    assert _postprocess_title("") == ""


def test_postprocess_title_caps_at_6_words():
    from channel.api.chats import _postprocess_title
    assert _postprocess_title(
        "One two three four five six seven eight",
    ) == "One two three four five six"


def test_postprocess_title_strips_wrapping_quotes():
    from channel.api.chats import _postprocess_title
    assert _postprocess_title('"Debug pytest fixture"') == "Debug pytest fixture"


def test_postprocess_title_returns_empty_on_preamble_only_truncation():
    """When the model wrote preamble but ran out of tokens before
    emitting the actual title, the colon-split returns an empty tail.
    The helper MUST surface that as empty (caller treats it as a
    failure outcome) — emitting the preamble itself as the title
    would be worse than 'New chat'."""
    from channel.api.chats import _postprocess_title
    assert _postprocess_title("Here's a 3-6 word title:") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_chats_api.py -k postprocess_title -v
```

Expected: 6 failures — `ImportError: cannot import name '_postprocess_title' from 'channel.api.chats'`.

- [ ] **Step 3: Implement `_postprocess_title` in `src/channel/api/chats.py`**

Read the file once to find a sensible insertion point (the existing module-level helpers like `_load_owned_chat`, `_agentcore_client`, etc. cluster near the top after imports). Add the new helper there:

```python
def _postprocess_title(raw: str) -> str:
    """Normalize raw titler output into a usable sidebar title.

    Haiku occasionally emits preamble (e.g. "Here's a 3-6 word
    title: Debug pytest fixture"). We strip leading explanations by
    taking the part after the LAST colon if a colon is present, then
    split into words and cap at 6 (matches the titler's 3-6 word
    system prompt). Strips wrapping quote characters and trailing
    sentence punctuation.

    Returns empty string if no usable text remains — caller treats
    that as a failure outcome (better to leave "New chat" than to
    surface preamble as a title).
    """
    if not raw:
        return ""
    candidate = raw.rsplit(":", 1)[-1] if ":" in raw else raw
    candidate = candidate.strip().strip('"').strip("'").strip()
    words = candidate.split()
    if not words:
        return ""
    return " ".join(words[:6]).rstrip(".,;:!?")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_chats_api.py -k postprocess_title -v --cov=channel.api.chats --cov-report=term-missing
```

Expected: 6 PASS. 100% line coverage on the new `_postprocess_title` function (verify by inspecting the coverage report — no uncovered line numbers in the function's lines).

- [ ] **Step 5: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "feat(channel-rd): _postprocess_title helper for titler salvage"
```

---

## Task 2: Salvage `MaxTokensReachedException` in the titler block

**Files:**
- Modify: `src/channel/api/chats.py`
- Modify: `tests/unit/test_chats_api.py`

Wire the salvage path into `_stream_bedrock_reply`'s titler block. The pure helper from Task 1 does the heavy lifting; this task is just the try/except restructure plus the integration tests.

- [ ] **Step 1: Write the failing tests**

The existing Phase 7d auto-title test in `tests/unit/test_chats_api.py` covers the happy path (titler completes, SSE emitted, success metric). Read it once first to understand the test harness shape (mock target for `build_titler_agent`, how the SSE stream is captured, how metrics are mocked). Mirror that shape.

Append:

```python
# ---- Titler salvage on MaxTokensReachedException ----------------------------

@pytest.mark.asyncio
async def test_titler_salvages_partial_output_on_max_tokens(monkeypatch, ...):
    """When Strands raises MaxTokensReachedException mid-stream, the
    already-accumulated title_chunks survive and produce a salvaged
    title via _postprocess_title. SSE emitted, success metric."""
    from strands.types.exceptions import MaxTokensReachedException

    async def fake_stream(_prompt):
        # Mimic Strands: emit deltas, then raise.
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Here's a title: "}}}}
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Debug pytest fixture"}}}}
        raise MaxTokensReachedException("token limit reached")

    fake_titler = MagicMock()
    fake_titler.stream_async = fake_stream
    monkeypatch.setattr(
        "channel.api.chats.build_titler_agent",
        lambda: fake_titler,
    )
    record = AsyncMock()
    monkeypatch.setattr(
        "channel.api.chats.record_auto_title_outcome",
        record,
    )
    # ... use the same _stream_bedrock_reply test harness from the
    # existing happy-path test. Drive a first-turn message; collect SSE
    # frames; assert sse_title_suggested with title="Debug pytest fixture";
    # assert record.assert_awaited_with(success=True).


@pytest.mark.asyncio
async def test_titler_records_failure_when_only_preamble_emitted(monkeypatch, ...):
    """If MaxTokensReachedException fires before any post-colon text
    arrives, the salvage returns empty and we MUST NOT emit SSE."""
    from strands.types.exceptions import MaxTokensReachedException

    async def fake_stream(_prompt):
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Here's a 3-6 word title:"}}}}
        raise MaxTokensReachedException("token limit reached")

    fake_titler = MagicMock()
    fake_titler.stream_async = fake_stream
    monkeypatch.setattr(
        "channel.api.chats.build_titler_agent",
        lambda: fake_titler,
    )
    record = AsyncMock()
    monkeypatch.setattr(
        "channel.api.chats.record_auto_title_outcome",
        record,
    )
    # Drive the harness; assert NO sse_title_suggested frame in collected SSE;
    # assert record.assert_awaited_with(success=False).


@pytest.mark.asyncio
async def test_titler_outer_except_catches_unrelated_errors(monkeypatch, ...):
    """Network errors / agent build errors still hit the outer
    except Exception and record failure — fail-soft preserved."""
    async def fake_stream(_prompt):
        raise RuntimeError("bedrock down")
        yield {}  # unreachable, satisfies async generator
    fake_titler = MagicMock()
    fake_titler.stream_async = fake_stream
    monkeypatch.setattr(
        "channel.api.chats.build_titler_agent",
        lambda: fake_titler,
    )
    record = AsyncMock()
    monkeypatch.setattr(
        "channel.api.chats.record_auto_title_outcome",
        record,
    )
    # Drive harness; assert NO sse_title_suggested; record.assert_awaited_with(success=False).
```

**Translate the `(monkeypatch, ...)` test fixtures + harness invocation to whatever the existing Phase 7d auto-title test uses** — match the existing patterns exactly. The pseudocode above shows the contract; the real test bodies use the project's actual test harness.

The exact event-yield shape for `fake_stream` depends on what `translate_event` consumes. Read `src/channel/agents/strands_sse.py` once to confirm — the harness should produce events that `translate_event` interprets as `kind == "delta"` with `payload` = the text chunk. If unsure, mimic the shape from the existing successful auto-title test.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_chats_api.py -k "titler_salvages or titler_records or titler_outer_except" -v
```

Expected: 3 failures — the current titler block doesn't catch `MaxTokensReachedException` inside the streaming loop, so the salvage test fails (no SSE emitted) and the preamble-only test passes accidentally (because the generic `except Exception` already swallows). After Task 2 lands all three should pass intentionally.

- [ ] **Step 3: Implement — restructure the titler block**

In `src/channel/api/chats.py`:

(a) Add the import at the top of the file alongside the existing imports:

```python
from strands.types.exceptions import MaxTokensReachedException
```

(b) Find the existing titler block in `_stream_bedrock_reply` (around line 325-359; starts with the `if was_first_round_trip and ...:` line). Replace the whole block with:

```python
    if was_first_round_trip and os.environ.get("STARTER_AUTO_TITLE_ENABLED", "1") == "1":
        truncated = False
        try:
            titler = build_titler_agent()
            titler_prompt = f"User: {user_message}\nAssistant: {assistant_text[:500]}"
            title_chunks: list[str] = []
            try:
                async for event in titler.stream_async(titler_prompt):
                    kind, payload = translate_event(event)
                    if kind == "delta":
                        title_chunks.append(payload)
            except MaxTokensReachedException:
                # Strands emits deltas BEFORE raising on token-cap,
                # so title_chunks already holds usable partial text.
                # _postprocess_title strips preamble and caps at 6
                # words; empty result → caller records failure.
                truncated = True
            title = _postprocess_title("".join(title_chunks))
            if title:
                storage.patch_chat(
                    user_id=claims["sub"],
                    chat=chat,
                    title=title,
                    archived=None,
                )
                yield sse_title_suggested(chat_id=chat.chat_id, title=title)
                await record_auto_title_outcome(success=True)
            else:
                await record_auto_title_outcome(success=False)
        except Exception as exc:
            logger.warning(
                "auto_title_failed chat_id=%s truncated=%s",
                chat.chat_id,
                truncated,
                extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                exc_info=True,
            )
            await record_auto_title_outcome(success=False)
```

Notes:
- `truncated` is declared at the top of the outer try so the outer `except` can still reference it (logged as `extra` context).
- The inner `except MaxTokensReachedException` deliberately doesn't re-raise — it falls through to the salvage path.
- All other exceptions go to the outer `except Exception` (unchanged fail-soft contract).
- The existing `_postprocess_title` strip-and-trim removed the in-place `.strip().strip('"').strip(".")` chain; that logic lives in the helper now.

- [ ] **Step 4: Run tests + coverage**

```bash
uv run pytest tests/unit/test_chats_api.py -k titler -v --cov=channel.api.chats --cov-report=term-missing
```

Expected: all PASS (including the existing happy-path test from Phase 7d). 100% coverage on the new branches in `_stream_bedrock_reply`.

- [ ] **Step 5: Commit**

```bash
git add src/channel/api/chats.py tests/unit/test_chats_api.py
git commit -m "fix(channel-rd): salvage titler partial output on MaxTokensReachedException"
```

---

## Task 3: CHANGELOG sync

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Append entry**

Add the following bullet to `[Unreleased]` under `### Fixed` (create the subsection if needed; sit it alongside any existing fix bullets):

```markdown
- Auto-titler now salvages partial output when the Haiku titler trips
  `MaxTokensReachedException`. Previously the whole title was
  discarded and the chat stayed on "New chat" in the sidebar. The
  salvage path strips colon-prefixed preamble (e.g. "Here's a title:
  ...") and caps the result at 6 words. Closes #90.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(channel-rd): CHANGELOG entry for titler salvage"
```

---

## Task 4: Pre-push + PR

- [ ] **Step 1: Pre-push gate**

```bash
uv run inv pre-push
```

Expected: `All checks passed!`. If ruff format complains, apply:

```bash
uv run ruff format src tests
git add -u && git commit -m "style(channel-rd): ruff format on titler-salvage files"
```

- [ ] **Step 2: Rebase + push**

```bash
git fetch origin
git rebase origin/development
git log --oneline origin/development..HEAD  # confirm only your commits
git push -u origin fix/channel-rd-titler-salvage:fix/channel-rd-titler-salvage
```

- [ ] **Step 3: Open PR with auto-merge**

```bash
gh pr create --base development \
  --title "fix(channel-rd): salvage auto-titler partial output on MaxTokens" \
  --body "$(cat <<'EOF'
## Summary

Catches ``strands.types.exceptions.MaxTokensReachedException`` specifically inside the titler streaming loop in ``_stream_bedrock_reply``. The exception is raised by Strands AFTER the model has emitted streaming delta events, so the partial output is already in ``title_chunks`` — previously it was discarded by the generic ``except Exception``.

New module-level ``_postprocess_title(raw)`` helper:
- Strips colon-prefixed preamble (``"Here's a title: ..."`` → ``"..."``)
- Caps at 6 words (matches the titler system prompt's "3-6 words" instruction)
- Strips wrapping quotes + trailing sentence punctuation
- Returns ``""`` if nothing usable remains — caller records failure, no SSE emitted, sidebar stays on "New chat"

Closes #90

## Test plan

- [x] Layer 1 — ``inv pre-push`` green. New ``_postprocess_title`` unit tests cover pass-through, colon-preamble strip, 6-word cap, quote strip, empty-input, preamble-only truncation. New streaming tests cover salvage on MaxTokens, preamble-only-MaxTokens failure, non-MaxTokens exception still fail-soft.
- [ ] Layer 2 — after merge + dev deploy: send a deliberately verbose first message ("Help me debug something complicated about pytest fixtures and tmp_path"). Sidebar title should change from "New chat" to a 3-6 word summary within ~5s of the assistant reply landing.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"

gh pr merge --auto --squash --delete-branch
```

This PR is **agent-safe eligible** (no infra, single backend file + test + CHANGELOG; matches the criteria in CLAUDE.md §Special labels). The issue #90 doesn't currently carry the ``agent-safe`` label — either add it before opening the PR, or skip the auto-merge step and merge manually.

- [ ] **Step 4: Watch CI**

```bash
gh run watch
```

Address any failures inline (likely candidates: ruff format on the new tests; coverage gap if a branch in ``_stream_bedrock_reply`` isn't exercised).

---

## Done criteria

- ✅ `_postprocess_title` exists and round-trips clean titles unchanged; strips colon preamble; caps at 6 words.
- ✅ Titler block catches `MaxTokensReachedException` specifically; salvage path persists + emits SSE when post-processed title is non-empty; empty result records failure metric.
- ✅ Outer `except Exception` still catches everything else (no regression in fail-soft contract).
- ✅ 100% coverage on the new helper + new branches.
- ✅ CHANGELOG `[Unreleased]` `### Fixed` has the bullet.
- ✅ Live verification on dev: verbose first message → real title, not "New chat".
