# Web search via Exa (#182) — design

**Date:** 2026-06-07
**Status:** Approved (ready for plan)

## Summary

Web search is the first concrete tool to ship on top of the chassis (#181, epic #128). This spec records the design decisions made during brainstorming so the implementation plan and reviewers can refer back to one place. Vendor choice (Exa), the wrap-not-build approach, and the `tools=[...]` registration shape were already decided by the [epic #128 sequencing strategy spec](2026-06-06-epic-128-tool-use-sequencing.md) and the [#128 decisions comment](https://github.com/warlordofmars/channel/issues/128#issuecomment-4617554628). This doc fills in the v1-specific shape: tool surface, error semantics, citation rendering channel, and rate-limit policy (or lack thereof).

## Goals

- Ship one tool the model can call to ground its replies in current web sources.
- Land citations naturally — verifiable, clickable, no new SSE protocol surface.
- Match the phenomenology preamble from #128: "like having hands" extends to "and now I can also reach beyond what's in this conversation."

## Non-goals

- Rendering a structured bibliography / footer cite list. That stays a future enhancement if real usage shows it's worth the protocol cost.
- Domain-specific search (academic-only, news-only, etc.) at v1. The `category` param exposes Exa's discriminators if the model wants them, but the default is general web.
- Per-user / per-day rate limits. The chassis chain-cap (8 tools/chain) is the v1 budget. If cost or abuse pressure materializes, file a follow-up.
- A separate `get_contents` tool. `exa_search` with `text=True` returns content alongside results in one call; that covers the v1 use case.

## Plan

### Architecture

```
User question that needs facts
  → Model decides to call web_search(query, ...)
    → src/channel/agents/tools/web_search.py wrapper
      → strands_tools.exa.exa_search(query, ..., text=True, livecrawl="fallback")
        → Exa API (https://api.exa.ai)
  → Tool returns {results: [{title, url, snippet, text}, ...]}
  → Model reads results in its next round-trip
  → Model writes the reply with inline markdown links: "...as covered [in this 2024 paper](https://example.com)"
  → SPA renders the markdown via the existing pipeline; links are clickable
```

The chassis pieces all stay unchanged. `web_search` enters the agent the same way `current_time` does (via `tools=[...]` in `build_agent()`, gated by an env-var kill switch in `chats.py`'s registration block). The four SSE event types from PR-2 emit naturally because the chassis fires them on any tool call — no `web_search`-specific protocol code.

### Tool surface

`src/channel/agents/tools/web_search.py` exposes one `@tool`:

```python
@tool
def web_search(
    query: str,
    num_results: int = 5,
    category: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> dict[str, Any]:
    """Search the web and return relevant pages with content.

    Use when you need facts from sources outside the conversation —
    current events, recent papers, specific URLs, etc.

    When you incorporate findings into your reply, cite them as inline
    markdown links: `[the relevant claim](https://example.com/page)`.
    Don't dump raw search results back to the user — distill them.

    Args:
        query: The search query string.
        num_results: How many results to return (1-10, default 5).
        category: Optional filter — one of ``"news"``, ``"research paper"``,
            ``"github"``, ``"pdf"``, ``"company"``, or ``None`` for general
            web search.
        include_domains: Optional list of domains to restrict to.
        exclude_domains: Optional list of domains to exclude.
    """
```

Hidden from the model (set server-side, not surfaced as params):

| Hidden param | Server-side default | Why |
| --- | --- | --- |
| `text` | `True` | Always pull page content alongside results — that's what makes one call sufficient |
| `livecrawl` | `"fallback"` | Fetch live if cached page is stale; don't fail closed if fresh fetch is slow |
| `type` | omitted (Exa's `"auto"` default) | Let Exa decide search shape; no model-tunable knob saves prompt tokens |
| `highlights`, `summary`, `subpages`, etc. | omitted | The model has the page text via `text=True`; these only add cost |

`num_results` is clamped to `[1, 10]` in the wrapper. Exa accepts up to 100 but chat-context can't usefully consume that many.

### Hidden invariants

- `text=True` is non-negotiable. If a future change makes it caller-tunable, the citation contract breaks (the model needs the content to write inline-link replies).
- Tool name **is `web_search`**, not `exa_search`. The model and the META event don't need to know the vendor. If we ever swap Exa for something else, only the wrapper's internals change.
- The tool docstring nudges the model toward inline markdown citations. The global `DEFAULT_SYSTEM_PROMPT` in `src/channel/agents/chat_agent.py` does NOT change. Per-tool prompting is cheaper than global system-prompt edits.

### Configuration + secrets

**Env vars on the API Lambda:**

| Var | Default jc/dev | Default prod | Meaning |
| --- | --- | --- | --- |
| `STARTER_WEB_SEARCH_ENABLED` | `1` | `1` | Kill switch — `chats.py` skips tool registration if `0` |
| `STARTER_EXA_API_KEY_PARAM` | `/channel/jc/exa-api-key` | `/channel/prod/exa-api-key` | SSM parameter **name** (path), injected by CDK at deploy time. `_resolve_exa_api_key()` fetches the value via boto3 on the first `web_search()` invocation and caches it via `@functools.lru_cache(maxsize=1)` for the lifetime of the Lambda warm pool. Mirrors `STARTER_JWT_SECRET_PARAM` in `src/channel/auth/tokens.py::_jwt_secret` |
| `EXA_API_KEY` (optional, local dev) | unset (or set manually) | unset | If set, short-circuits the SSM fetch. Used by `inv dev`, which pulls the jc key from SSM into this env var at startup |

`STARTER_WEB_SEARCH_ENABLED=1` in **all** environments for v1 — there's no progressive rollout plan; the flag exists for emergency kill, not staged enable. (Contrast with `STARTER_CLOCK_TOOL_ENABLED` which is intentionally off in prod because `current_time` is a smoke-test tool, not a product feature.)

**SSM parameters** (created externally, not by CDK; CDK only grants read):

- `/channel/jc/exa-api-key`
- `/channel/dev/exa-api-key`
- `/channel/prod/exa-api-key` — set when prod ships

**CDK changes** (`infra/stacks/channel_stack.py`):

- Grant the API Lambda role `ssm:GetParameter` on `/channel/{env}/exa-api-key` (per-env resource ARN, parameterized).
- Inject the SSM parameter **path** as the `STARTER_EXA_API_KEY_PARAM` Lambda env var (NOT the value — the value resolves at runtime on first `web_search()` call via boto3 `ssm.get_parameter`, mirroring how `STARTER_JWT_SECRET_PARAM` flows in `src/channel/auth/tokens.py::_jwt_secret`).
- Set `STARTER_WEB_SEARCH_ENABLED=1` in the env vars block alongside the chassis flags.

**`chats.py` registration:**

```python
tool_registry: list[Any] = []
if os.environ.get("STARTER_CLOCK_TOOL_ENABLED") == "1":
    tool_registry.append(current_time)
if os.environ.get("STARTER_WEB_SEARCH_ENABLED") == "1":
    from channel.agents.tools.web_search import web_search
    tool_registry.append(web_search)
```

Lazy import so the existing chassis stays unaffected when the flag is off (no Exa SDK import at startup).

**`inv dev` local-smoke handling:** before this PR ships, `tasks.py` needs to also pull `EXA_API_KEY` from SSM (or surface a clear error if missing). The plan will spec the smallest change there.

### Citation flow (no SSE protocol change)

This was the design decision worth recording explicitly because it deviates from how the [#128 decisions comment](https://github.com/warlordofmars/channel/issues/128#issuecomment-4617554628) sketched it.

PR-2 round-5 narrowed `sse_tool_finished.summary` to the literal `"completed"` — raw tool output never reaches the SPA over SSE. This rules out a structured bibliography channel for v1 without re-opening that contract.

**v1 citation path:** the model writes the reply with inline markdown links (`[claim text](https://url)`). The SPA's existing markdown renderer handles them as standard clickable links. Each cited claim points at its source; verification is per-link click. No footer bibliography, no superscript numbers, no new `ToolResultBlock` branch.

**`ToolResultBlock` policy P3 deferral:** the chassis design doc said `kind="web-search-citations"` would land here. With markdown-link citations as the v1 channel, this branch isn't needed. The seed component stays at its current shape (default text summary). If usage shows the inline-link UX feels missing — academic-style numbered footnotes, hover previews, dedicated sources panel — file a follow-up that opens both the SSE protocol question AND the `ToolResultBlock` extension at the same time.

### Error handling

`exa_search` can fail four ways. The wrapper catches each and returns a structured error dict that Strands packages into a `ToolResult` with `status="error"`. PR-2 round-1 fix preserves the reason string via `error_type`, so the SPA receives it via `sse_tool_error`.

| Failure | `error_type` | What the model sees / can do |
| --- | --- | --- |
| `httpx.ReadTimeout` (Exa > 30s) | `"timeout"` | Retry with rephrased query, or abandon |
| `httpx.HTTPStatusError` 5xx | `"upstream_5xx"` | Same as timeout — transient, can retry |
| `httpx.HTTPStatusError` 429 (Exa-side throttle) | `"rate_limit"` | Surface "I'm throttled, try again later" — don't retry |
| `httpx.HTTPStatusError` 4xx (bad query, invalid filters) | `"bad_request"` | Rephrase, or give up |
| `{results: []}` (no matches) | NOT an error — `status="success"` | Model says "I couldn't find sources for that" |

Timeout = 30s. Long enough for `livecrawl="fallback"` to fetch a slow page if needed; short enough that the chassis wall-clock budget (120s) survives multiple search attempts within the same chain. The chain-cap (8 tools/chain from PR-1) is the broader safety net against runaway behavior.

**No in-wrapper retry.** First failure surfaces immediately. The model decides whether to retry (same query, different query, or abandon). Wrapper-internal retries would compete with the `ChainState.tool_calls_used` counter and confuse the failure semantics.

### Tests

**Unit tests** (`tests/unit/test_tools_web_search.py`):

- `test_web_search_returns_strands_tool_result_on_success` — patch `strands_tools.exa.exa_search` to return a fake response; assert the wrapper returns the expected dict shape with `results`.
- `test_web_search_clamps_num_results_to_10` — call with `num_results=50`; assert wrapper passes `10` to `exa_search`.
- `test_web_search_clamps_num_results_to_1` — call with `num_results=0`; assert wrapper passes `1`.
- `test_web_search_always_passes_text_true_and_livecrawl_fallback` — caller can't override these; assert they're set server-side regardless.
- `test_web_search_timeout_returns_error_status` — patch `exa_search` to raise `httpx.ReadTimeout`; assert `{"status": "error", "error_type": "timeout"}`.
- `test_web_search_upstream_5xx_returns_error_status` — `httpx.HTTPStatusError(status_code=500)`; assert `error_type="upstream_5xx"`.
- `test_web_search_429_returns_rate_limit` — assert `error_type="rate_limit"`.
- `test_web_search_4xx_returns_bad_request` — assert `error_type="bad_request"`.
- `test_web_search_empty_results_is_success_not_error` — Exa returns `{results: []}`; assert success status (no error).
- `test_web_search_is_a_strands_tool` — `hasattr(web_search, "tool_spec")` per the same probe pattern as PR-1 Task 4.

**Chats route tests** (`tests/unit/test_chats_api.py`):

- `test_post_message_registers_web_search_tool_when_flag_on`
- `test_post_message_omits_web_search_tool_when_flag_off`

**CDK assertion tests** (`tests/unit/test_channel_stack.py`):

- `test_api_lambda_has_exa_api_key_env_var_referencing_ssm`
- `test_api_lambda_role_has_ssm_read_access_to_exa_key_path`
- `test_prod_stack_sets_web_search_enabled_to_1`
- (and `test_dev_stack_sets_web_search_enabled_to_1` for symmetry)

**E2e smoke test** (`tests/e2e/test_web_search_smoke.py`):

- Pattern from `tests/e2e/test_tool_use_smoke.py`.
- Gated on `STARTER_WEB_SEARCH_ENABLED=1` AND `EXA_API_KEY` set (otherwise skip).
- Send a query that needs a fresh source: "search for the latest news on retrieval augmented generation" (or similar; the wording matters — too generic and the model may skip the tool).
- Assert: at least one `tool_started` with `tool_name="web_search"`, matching `tool_finished` with `summary="completed"`, NO `tool_error` for that `tool_use_id`, AND the model's reply text contains at least one inline markdown link (`re.search(r'\[[^\]]+\]\(https?://[^)]+\)', reply_text)`).
- Best-effort `DELETE /api/chats/{id}` in `finally` (mirrors `test_tool_use_smoke.py`).

**100% Python coverage** required on the new wrapper module. All unit tests patch the upstream `exa_search` — no live Exa calls in unit / integration tests.

### Live verification (post-merge)

Same pattern as the #181 chassis: after this PR merges and channel-dev redeploys, you smoke-test in the chat UI:

1. Ask Channel something that needs current facts. Suggested prompts:
   - *"What's the latest paper on retrieval-augmented generation?"*
   - *"What are some recent developments in [a topic you haven't discussed with Channel before]?"*
2. Expect:
   - Collapsible step list shows `> 1 tool step` with `web_search — finished` (or `running` flickering during the call)
   - Reply contains at least one inline markdown link to a real URL you can click
   - The link goes somewhere real (not hallucinated)
3. If the citation links work and feel natural in the prose, the v1 is shipped. If the model writes the reply without links (just naked URLs or no citation at all), the tool docstring's nudge isn't strong enough — file a follow-up to either strengthen the docstring or move citation guidance to `DEFAULT_SYSTEM_PROMPT`.

## Risks

### R1 — Model doesn't actually use the inline-link citation pattern

The tool docstring tells the model to write `[claim](url)`. Models are inconsistent about following instructions inside tool docstrings vs. system prompts. If real usage shows the model often writes naked URLs or no citations:

- First mitigation: strengthen the docstring with a single-shot example.
- Second mitigation: move the citation nudge to `DEFAULT_SYSTEM_PROMPT` (cheap to do, costs a few prompt tokens per turn).
- Third mitigation: switch to a structured citation channel (the deferred SSE protocol extension). Most invasive.

### R2 — No rate limit means a runaway chain can rack up Exa cost

The chain-cap (8 tools/chain) is the only v1 limit. At Exa free tier (~1000 searches/month) and ~$5 per 1000 thereafter, a single abuser chatting all day at 8 searches/turn × 100 turns/day × 8 = ~6400 searches/day per user. Mitigated by:

- The free tier itself is the soft limit (Exa starts charging when we exceed it).
- Real users don't chat at saturating volume.
- If cost surfaces as a real constraint, file `feat: per-user rate limit for web_search` and add the DDB counter.

### R3 — Exa contractually doesn't honor no-training

Brainstorm flagged this as a precondition. If the public terms don't say "no training on customer data" clearly, get it in writing before shipping. If Exa won't commit, vendor decision re-opens. Already on the user's plate.

### R4 — `text=True` makes search slower / more token-heavy than necessary

`text=True` fetches page content for each result. For 5 results, that's ~5x the response size and noticeable latency. Mitigated by:

- `livecrawl="fallback"` uses cached content when available.
- 30s timeout is generous enough.
- The 5-result default is small; bumping to 10 should be deliberate.

If real usage shows the model only uses 1-2 of the 5 results' content, consider lowering the default to 3 or switching to a two-tool pattern (`web_search` for URLs + titles only, `web_search_contents` for the full text of one or two URLs).

### R5 — `inv dev` local stack needs `EXA_API_KEY`

Not a runtime risk; a developer-experience one. If the plan's `tasks.py` change is missed, `inv dev --seed` will fail when the web_search tool gets registered. Mitigated by:

- Plan must call this out explicitly.
- Failure mode is loud (Lambda startup error), not silent.

## Cross-references

- [Strategy spec for epic #128](2026-06-06-epic-128-tool-use-sequencing.md)
- [Chassis design doc (#181)](2026-06-06-tool-use-chassis-design.md)
- [MCP-registry spike (#184)](2026-06-06-mcp-server-registry-spike.md) — informational; MCP is a parallel direction
- [#128 decisions comment](https://github.com/warlordofmars/channel/issues/128#issuecomment-4617554628) — vendor + meta decisions
- Issue: [#182](https://github.com/warlordofmars/channel/issues/182)
- Strands probe references:
  - `strands_tools.exa.exa_search` — wrapped tool
  - `strands_tools.exa.exa_get_contents` — NOT used in v1 (one-tool pattern instead)

## Follow-up

After this spec is approved:

1. **Issue body refresh on #182** — soften the rate-limit task ("per-user DDB counter") to "deferred for v1; chain-cap is the de-facto limit"; replace the SPA "citation rendering for `source_type: web_search` in `Conversation.jsx`" task with "no SPA change — citations are inline markdown links handled by the existing renderer."
2. **`writing-plans` produces the implementation plan** — single PR, design doc + implementation + tests + CHANGELOG, mirroring how #181's PR-3 packaged things. Estimated `size:m` work as the issue label says.
3. **Live verification on channel-dev after the PR merges.**
