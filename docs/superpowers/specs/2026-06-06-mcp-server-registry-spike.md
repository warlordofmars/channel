# Spike: MCP-server registry + per-chat selection (epic #128 part spike)

**Date:** 2026-06-06
**Status:** Spike output (1-week timebox per #184)
**Outcome:** File `#128-D` as `status:ready` (does not gate epic #128 close)

## TL;DR

The spike resolves the central question — Strands `MCPClient` vs `fastmcp.Client` —
by reading Strands' wrapper source. The constructor at
`strands/tools/mcp/mcp_client.py:116` takes a
`transport_callable: Callable[[], MCPTransport]`. That parameter is the
architectural seam: **Channel owns OAuth 2.1 + DCR + refresh lifecycle, hands
Strands a pre-authenticated transport thunk.** Strands acts as the
tool-invocation layer and never sees the bearer token. `MCPClient`
implements `ToolProvider`, so MCP-tool calls flow through the chassis's
`BeforeToolCallEvent` / `AfterToolCallEvent` hooks identically to native
tools — budget gating, chain-cap, memory META writes, and the collapsible
step-list UI all work uniformly. That last property is the criterion
Channel-the-product called out as load-bearing in the 2026-06-05 input.

For the four [#184] questions, the spike recommends:

1. **DDB registry table** (`PK=USER#{u}, SK=MCPSERVER#{id}`) + REST CRUD
   surface under `/api/mcp/servers`. Config-file upload and DCR-based
   discovery are both worse on UX and protocol grounds.
2. **Two-level selection** — a `MCP servers` section in `/app/customize`
   for the user's "globally enabled" toggle, plus a composer popover
   sibling of `ModelPicker` / `AttachMenu` for per-chat overrides. New
   chats inherit all globally-enabled servers; overrides are opt-out.
3. **One DCR client per `(user, server)` pair.** Tokens in DDB (sibling
   `PK=USER#{u}, SK=MCPTOKEN#{server_id}` row). Lazy refresh on every tool
   call; failures surface as `sse_tool_error` with `error_type: "auth_expired"`
   so the SPA can render a "Reconnect Hive" affordance. Per-tool-call
   `MCPClient` re-instantiation for v1 (~100-200ms handshake cost
   acceptable; a shared mutable-token closure is a v2 optimization if
   telemetry justifies).
4. **User-level for v1.** Workspace-level deferred until workspaces ship.
   Forward-compatible PK shape (`USER#{u}` → `WORKSPACE#{w}` later)
   keeps the migration cheap.

Two items are explicitly **out of v1** but pinned so they don't quietly
defer:

- **OAuth scopes UX** (Channel-the-product 2026-06-05) — server-provided
  vs Channel-side registry vs hybrid. v1 shows raw scopes only on the
  register-server page behind a disclosure; v2 picks a friendlier
  rendering after real users hit confusion.
- **Workspace-coupled tokens** — does the same Hive registration work
  across workspaces, or does each workspace re-register? Depends on the
  MCP server's principal model. v2.

## Goals

- Resolve the four [#184] questions concretely enough that an implementer
  can write a plan without a second design pass.
- Verify or reject the auth-boundary hypothesis — *Channel manages auth,
  Strands gets transport* — against actual Strands 1.41.0 source, not
  speculation.
- Pin the OAuth scopes UX as an explicit open question for v2 rather
  than letting it implicitly defer into a UX gap.

## Non-goals

- Implementing anything. Spike output is design only.
- Resolving every adjacent open question (workspace-level MCP selection,
  scope translation UI, server marketplace) — those are v2 or
  out-of-#128.
- Re-deriving anything resolved in epic [#128]'s decisions comment
  (vendor: Exa for web search, Lambda for code-exec, etc.). MCP is a
  separate vertical; those decisions don't apply.
- Designing the auth-code flow's redirect-URI handling, PKCE shape, or
  refresh-token rotation strategy in detail. Those are implementation
  details for `#128-D`'s plan.

## Architectural seam: Channel owns auth, Strands gets transport

### What the wrapper actually wants

`strands/tools/mcp/mcp_client.py:116-138`:

```python
def __init__(
    self,
    transport_callable: Callable[[], MCPTransport],
    *,
    startup_timeout: int = 30,
    tool_filters: ToolFilters | None = None,
    prefix: str | None = None,
    elicitation_callback: ElicitationFnT | None = None,
    tasks_config: TasksConfig | None = None,
) -> None: ...
```

`transport_callable` is a thunk returning an `MCPTransport`
(`(read_stream, write_stream)` tuple). Channel constructs an
authenticated HTTP transport — bearer token attached as a header — and
passes `lambda: authenticated_transport()`. Strands opens the transport
in its background thread (`_async_background_thread` at line 772) and
sees only the open streams. The token never crosses the seam.

### Concrete integration

```python
def make_authenticated_transport(server_url: str, token: str):
    def thunk():
        # mcp.client.streamable_http exposes a streamable_http_client
        # accepting headers= for the upstream connection.
        return streamable_http_client(
            server_url,
            headers={"Authorization": f"Bearer {token}"},
        )
    return thunk

client = MCPClient(
    make_authenticated_transport(server.url, token.access_token),
    prefix=server.tool_prefix,  # e.g. "hive"
)
```

This is the *entire* integration surface between Channel's auth layer
and Strands' tool layer. One line, zero abstraction.

### Why the auth boundary lives in Channel, not Strands

OAuth 2.1 + DCR + refresh is a non-trivial lifecycle:

- DCR registration shape varies per server (some return `client_secret`,
  some are public clients with PKCE-only).
- Auth-code flow needs redirect URIs Channel controls (`/auth/mcp/callback`).
- Refresh windows have jitter, retry semantics, and `auth_status`
  bookkeeping that the SPA must surface.
- Scope-update flows (server adds a tool that needs a new scope) require
  Channel-side UX, not framework-side plumbing.

Keeping this in `src/channel/mcp/` lets us iterate without waiting for
upstream Strands releases. Whether the implementation reuses
`fastmcp.Client`'s helper modules for the auth-code dance is an
implementation detail of `#128-D` — the architectural seam is set at
"Channel owns auth, Strands gets transport" regardless.

### Hooks property preserved

`MCPClient` is itself a `ToolProvider` (`class MCPClient(ToolProvider)`
at line 104). Each registered MCP server is appended to the agent's flat
`tools=[...]` list alongside native tools:

```python
# in build_agent() — chassis-side, #181
agent = Agent(
    model=...,
    system_prompt=...,
    tools=[
        current_time,                              # native (smoke-test)
        exa_search,                                # native (#182)
        code_exec,                                 # native (#183)
        MCPClient(make_authenticated_transport(...), prefix="hive"),
        MCPClient(make_authenticated_transport(...), prefix="ai_activate"),
    ],
)
```

Strands' event loop dispatches `BeforeToolCallEvent` /
`AfterToolCallEvent` uniformly across the list. Budget gating,
chain-length cap, memory META-fact writes, EMF counters, and the
collapsible step-list UI — every chassis behavior from #181 — applies
to MCP tools without special-casing.

This satisfies Channel-the-product's stated picking criterion: MCP tools
participate in the same hook surface as native tools, so the budget
envelope and step-list UI work uniformly. The Strands-vs-fastmcp
question is answered by *both* — Strands at the tool-invocation layer,
fastmcp libs available inside `src/channel/mcp/auth.py` if convenient.

## Question 1 — SPA discovery

### Decision

**DDB registry table** + REST CRUD. The SPA queries
`GET /api/mcp/servers` on app load, caches in a sibling of
`ChatsContext` (`MCPServersContext` or rolled into the existing context
if it stays small), and renders the resulting list in the Customize tab
and the composer popover.

### Why not "per-user config file uploaded by user"

- Worse UX for first add (file upload vs. form fill).
- Forces users to handle their own client-secret storage if we ever
  support pre-shared secrets.
- No discoverable shape; harder to validate; harder to surface
  per-server `auth_status` to the user.
- Server-list mutations require re-upload, not a single CRUD call.

### Why not "OAuth DCR discovery against a well-known endpoint"

- DCR is a *registration* protocol, not a *discovery* protocol. There's
  no canonical "list servers I've registered with" endpoint at the user
  level — DCR clients live at each MCP server.
- Discovery would require Channel to either crawl a registry of MCP
  servers (doesn't exist at protocol level today) or have the user know
  URLs upfront — which lands us back at "user types in a URL" anyway.

### DDB item shape

```
PK=USER#{user_id}
SK=MCPSERVER#{server_id}              # server_id = UUID
type=MCPSERVER
name="Hive"                           # user-facing label
url="https://hive.warlordofmars.net/mcp"
client_id="<DCR-issued>"              # opaque to Channel
client_secret_ssm_path="..."          # nullable; populated only for confidential clients
tool_prefix="hive"                    # passed to MCPClient(prefix=...)
auth_status="active"|"expired"|"never_authed"|"revoked"
globally_enabled=true                 # Customize-tab toggle
created_at=...
updated_at=...
```

Token storage is split into a sibling row (see Q3) so token writes
don't cause unrelated MCPSERVER reads to see uncommitted state, and so
the TTL semantics on tokens can be independent of the server row.

### REST surface

- `GET  /api/mcp/servers` — list user's registered servers.
- `POST /api/mcp/servers` — register new (body: `{name, url, tool_prefix?}`).
  Server runs DCR against the URL, persists `client_id`, returns
  `{server_id, auth_start_url}` where `auth_start_url` is the OAuth
  authorization endpoint Channel constructs (with PKCE, state, and a
  Channel-controlled redirect URI).
- `GET  /api/mcp/servers/{id}` — single-server read (for the per-server
  edit screen in Customize).
- `PATCH /api/mcp/servers/{id}` — update `name` / `tool_prefix` /
  `globally_enabled` only. URL changes are delete-and-re-register (URL
  change typically means a different server identity).
- `DELETE /api/mcp/servers/{id}` — revoke at server (best-effort) + delete
  MCPSERVER row + delete sibling MCPTOKEN row.
- `POST /api/mcp/servers/{id}/reauth` — re-trigger auth-code flow (token
  expired, scopes changed, user revoked at server). Returns
  `{auth_start_url}`.
- `GET  /auth/mcp/callback` — Channel-controlled redirect URI; exchanges
  the code for tokens, writes the MCPTOKEN row, returns the SPA to
  `/app/customize`. (Lives outside `/api/*` because it's a browser
  redirect target, not a JWT-authenticated endpoint; uses an OAuth
  `state` value that Channel mints and verifies.)

## Question 2 — Per-chat selection UX

### Decision

**Two-level selection:**

1. **Globally enabled** — a single boolean per server in the user's
   registry (the `globally_enabled` column above). Set on register
   (default `true`); toggled in `/app/customize` under a new
   "MCP servers" section.
2. **Active for this chat** — composer popover sibling of `ModelPicker`
   and `AttachMenu`, showing the currently-active server list with
   checkboxes; defaults to *all globally-enabled servers* but allows
   the user to opt out per chat. Override is persistent on the chat row
   (DDB item).

### Chat-scope persistence

Per-chat override stored as
`PK=CHAT#{chat_id}, SK=MCPSERVERS#META`:

```
type=CHAT_MCPSERVERS
mode="inherit"|"explicit"
explicit_server_ids=[...]   # only present when mode="explicit"
```

`mode="inherit"` is the default — chassis resolves to "all
globally-enabled servers as of now." Switching to explicit captures the
list at the moment of override. This keeps the data model simple while
still respecting "new global server added → existing inherited chats
pick it up; existing explicit chats don't pick it up automatically." The
latter is the right default — explicit override means *the user already
made a decision about this chat*.

Chat ownership is still enforced by `_load_owned_chat(chat_id, jwt_sub)`
in `src/channel/api/chats.py`; the MCPSERVERS#META row is just chat
metadata. No new scope-enforcement path needed.

### Defaults rationale

- **New server → globally enabled by default.** Users register a server
  because they want it; making them flip a second toggle to use it is
  friction.
- **New chat → inherits all globally-enabled servers.** Matches the
  "like having hands" preamble from #181: users expect all configured
  tools to be available unless this chat is scoped narrowly. Per-chat
  scope-down is the rare case; per-chat scope-up is impossible (can't
  use a server that isn't registered).
- **Per-chat override = opt-out only.** Selecting "all servers minus
  Hive for this chat" is more common than "this chat uses only Hive";
  the opt-out model handles the common case in one click.

### Why a composer popover, not a sidebar tab

- Tool selection is per-chat-turn context, conceptually adjacent to
  model selection and attachments. The composer is where users already
  think about "what goes into this turn."
- Customize is for persistent preferences; per-chat overrides belong
  with the rest of per-chat affordances.
- Matches the existing UI grammar — `<div className="backdrop" />` +
  `<div className="pop" />` pattern documented in CLAUDE.md's
  "Chat-app popovers" bullet. Reuses the established backdrop dismissal
  pattern.

### Visibility: which servers are active?

Composer renders an inline indicator (small pill) showing "N tools" with
the popover trigger. Click → popover shows server list + checkboxes;
expanded per-server shows tool count (e.g. "Hive: 12 tools"). Listing
per-server tool catalogs in the composer would clutter the UI — tool
names surface in the collapsible step list (#181) when tools actually
fire.

### OAuth scopes UX — pinned as open question

Per Channel-the-product's 2026-06-05 input, surfacing scopes in the
per-chat selection UX is **not v1**, but the spike pins three options to
revisit:

1. **Server-provided metadata** — MCP servers expose human-readable
   capability descriptions; Channel renders what the server says.
2. **Channel-side registry** — Channel maintains a curated map of
   known-server scopes → friendly descriptions; servers Channel doesn't
   know about fall back to raw scope strings.
3. **Hybrid** — prefer server-provided when available, fall back to
   Channel-side registry, fall back to raw scopes with a warning.

v1 ships option 4 (defer — show raw scopes only on the register-server
page, behind a "Permissions this server is requesting" disclosure). v2
picks one of 1/2/3 after real users hit confusion. The
register-server page MUST surface the scope list pre-grant — even raw —
because that's the consent moment; deferring inside the popover or in
Customize is fine.

### Trade-off worth flagging

The two-level model adds one more concept (global vs per-chat) over a
"globally configured + always used" alternative. Justification: per-chat
override is what makes the "narrow tool scope for this specific chat"
use case work (e.g. "draft an email — don't touch Hive memory for
this"). The cost is one extra UI affordance and one DDB row per
overridden chat; the benefit is the opt-out lever exists when users want
it. If telemetry post-launch shows the override is rarely used, we can
remove the popover and keep just the Customize toggle — but starting
without it means users hit the gap and we ship it reactively. The
calibration call: include in v1.

## Question 3 — Token-swap mechanics

### Decision

**One DCR client per `(user, server)` pair.** Tokens in DDB on a sibling
row keyed by `server_id`. Per-tool-call: chassis resolves the active
server's token from DDB, attaches as bearer header on the transport,
passes the authenticated thunk to Strands. Refresh: lazy check on every
tool call.

### Token storage shape

```
PK=USER#{user_id}
SK=MCPTOKEN#{server_id}         # sibling to MCPSERVER row
type=MCPTOKEN
access_token=<KMS-encrypted>    # encrypt-at-rest per dynamodb-item conventions
refresh_token=<KMS-encrypted>
expires_at=<epoch_seconds>
granted_scope="..."             # space-separated, as returned by token endpoint
updated_at=...
TTL=<expires_at + refresh_grace_window>  # auto-prune long-stale tokens
```

### Why DDB and not SSM

- Per-user-per-server rows → cardinality scales with `users × servers`.
  SSM is poor at high cardinality (per-account parameter limit ~10k by
  default; per-second `GetParameter` quotas).
- DynamoDB single-table design is already in place (CLAUDE.md
  "DynamoDB single table design" section). Adding two SK prefixes
  (`MCPSERVER`, `MCPTOKEN`) is well-trodden — see the `dynamodb-item`
  skill for naming conventions.
- Tokens are accessed on every tool call. DDB latency is well-understood
  in this codebase; SSM has unpredictable GetParameter latency
  under high QPS.

KMS-encrypt the token fields at the application layer. DynamoDB's
server-side encryption alone isn't enough — Lambda IAM principals
shouldn't see raw tokens at-rest if a CloudTrail principal-list audit
ever runs. (See ADR-0001 if it exists; otherwise this is a v1
implementation choice worth noting.)

### Refresh strategy

**Lazy check on every tool call:**

1. `BeforeToolCallEvent` handler reads the relevant MCPTOKEN row.
2. If `expires_at - now < 60s`, refresh first (synchronous;
   `~50-100ms` round-trip to the server's token endpoint), then
   write the new token row, then proceed with the tool call.
3. If `expires_at - now >= 60s`, use the cached token directly.

**No background pre-refresh in v1.** Adds Lambda warmth complexity
(scheduled refresher Lambda, cross-tenant scheduling, race conditions
between refresher and tool-call refresh). Lazy-check is simple, correct,
and adds <100ms to the rare tool call that catches the expiry window.

**Refresh failures** → MCPTOKEN row marked deleted (or `auth_status`
flipped on the MCPSERVER row), tool call returns `sse_tool_error` with
`error_type: "auth_expired"`. SPA renders an inline "Reconnect Hive"
affordance that triggers `POST /api/mcp/servers/{id}/reauth`.

### Passing tokens to MCPClient

Per the architectural seam: chassis constructs the
`transport_callable` thunk at agent-build time, capturing the resolved
token:

```python
def _build_mcp_clients_for_chat(jwt_sub: str, chat_id: str) -> list[MCPClient]:
    active_servers = registry.list_active_servers_for_chat(jwt_sub, chat_id)
    clients = []
    for server in active_servers:
        token = tokens.get_valid(jwt_sub, server.server_id)  # refreshes if expiring
        clients.append(
            MCPClient(
                make_authenticated_transport(server.url, token.access_token),
                prefix=server.tool_prefix,
            )
        )
    return clients

# build_agent (chat_agent.py)
agent = Agent(
    ...,
    tools=[*native_tools, *_build_mcp_clients_for_chat(jwt_sub, chat_id)],
)
```

### Mid-chain refresh edge case

If a token expires partway through a chain (say a 30-minute token, a
6-minute chain — won't happen in practice but the design must cover
it), the existing `MCPClient` instance carries a stale token in its
already-open transport.

Two options:

1. **Re-instantiate `MCPClient` per tool call.** Simplest. Cost: per-call
   MCP handshake (~100-200ms via `_async_background_thread` startup).
2. **Mutable token reference inside the transport_callable's closure.**
   Refresh writes the new token into a shared `box`; next transport open
   reads from `box`. Requires Channel-side state but avoids the per-call
   handshake.

**v1 picks (1)** — simpler, no shared mutable state, fits Lambda's
stateless model. The 100-200ms cost is bounded; the chain wall-clock
budget (`~2 min` per #128 decision 4) accommodates it. (2) becomes a
follow-up optimization if telemetry shows high tool-call rates per chain
and per-call handshake overhead dominates.

### Why one DCR client per `(user, server)` pair, not one per user

A single user with two registered MCP servers needs two distinct DCR
client registrations — each server runs its own DCR endpoint and issues
its own `client_id`. Trying to reuse one DCR registration across servers
is a protocol violation. The PK/SK shape above already encodes this; the
note here is just to make the reasoning explicit.

A single user with one server registered twice (e.g. dev + prod Hive)
also gets two distinct registrations because they're different `url`
values. URL is the identity discriminator at the DCR-client level.

## Question 4 — Workspaces coupling

### Decision

**User-level for v1.** Workspace-level deferred until workspaces ship.

### Why

CLAUDE.md's "Workspaces are the tenancy root" product decision means
*eventually* MCP server registration should live at the workspace
level — same workspace, same tool registry; switching workspace
switches tools. But:

- Workspaces aren't shipped yet (no DDB taxonomy, no UI, no auth-claim
  for `workspace_id`).
- Forcing workspace-level now requires a `workspace_id` field on every
  MCPSERVER row + UI affordance to select one. That's premature design
  for non-existent infra.
- User-level today + a forward-compatible PK shape (`USER#{user_id}` →
  `WORKSPACE#{workspace_id}` later) lets us migrate cleanly when
  workspaces land.

### Migration path

When workspaces ship:

1. Add `workspace_id` to MCPSERVER + MCPTOKEN rows (during a one-time
   backfill: assign each row to the user's default workspace).
2. New PK structure: `WORKSPACE#{workspace_id}` for new rows; existing
   rows migrated lazily on next access (read the user's default-workspace
   claim from JWT; rewrite the row's PK).
3. Token-swap-via-claim becomes possible per CLAUDE.md's "Agents swap
   tokens to switch context": per-workspace JWT carries the
   `workspace_id`; chassis filters MCP servers by
   `workspace_id == jwt.workspace_id`.

This spike doesn't ship the migration; it just avoids painting itself
into a corner. Specifically: don't add `workspace_id=null` on v1 rows
(adding the field later is cheaper than migrating a placeholder);
don't expose `workspace_id` in the v1 REST API surface.

### Per-workspace token isolation — explicit v2

Today: tokens are per-user-per-server. Tomorrow with workspaces: tokens
become per-workspace-per-server. Token-row PK migration is the same as
the MCPSERVER migration above. The DCR client itself may or may not
need re-registration depending on whether the MCP server treats the
workspace as a separate principal — that's a v2 question (depends on
the MCP server's policy; Hive's stance, for instance, isn't decided
yet). This spike notes it and moves on.

## MCP-as-Sampling-server vs MCP-as-tool-server

Explicit one-paragraph callout (mirrors the decision-comment §8
callout on #128 to prevent future confusion):

CLAUDE.md's "client-side LLM preferred" product decision governs MCP
**Sampling** — the protocol direction where an MCP server asks the
client (us) for an LLM completion. This spike covers the **other**
direction: MCP-as-tool-server, where an MCP server provides external
capability (search, memory, file access) and we are the consumer. The
product decision does not constrain this direction. A future decision
about Sampling support is a separate design pass; nothing in this
spec presumes the Sampling-server role.

## Open questions (for `#128-D` or v2)

| # | Question | Disposition |
|---|---|---|
| 1 | OAuth scopes UX (Channel-the-product 2026-06-05) — server-provided vs Channel-side registry vs hybrid | v2; v1 shows raw scopes only on the register-server consent page |
| 2 | Per-workspace token isolation — same Hive registration across workspaces, or re-register per workspace? | v2; depends on MCP server's principal model |
| 3 | Server marketplace — Channel-curated list of "popular MCP servers" with one-click register | Out of #128; deferred |
| 4 | Tool prefix collisions — two servers expose `search`; how does the SPA disambiguate in the step-list UI? | v1: `MCPClient(prefix=...)` mandatory on register; SPA shows prefixed names. Resolved here. |
| 5 | Per-server tool subsetting — `MCPClient.tool_filters` lets us deny specific tools. Expose in UI? | v2 once we see real usage patterns |
| 6 | Elicitation callbacks — `MCPClient.elicitation_callback` lets servers ask the client for more info mid-call. SPA wiring? | v2; for v1, MCP servers that elicit fail gracefully via `sse_tool_error` with `error_type: "elicitation_unsupported"` |
| 7 | MCP-as-Sampling-server (server asks Channel for LLM completion) | Out of #128; separate design pass governed by "client-side LLM preferred" product decision |

## Recommended outcome

**File `#128-D` (MCP-server registry + auth implementation) as
`status:ready`.** Not `status:design-needed` — the design is
sufficiently converged that an implementer can write a plan against
this spec without a second design pass.

### `#128-D` scope sketch

- `src/channel/mcp/auth.py` (new) — DCR registration + auth-code flow
  initiation + token exchange + refresh.
- `src/channel/mcp/registry.py` (new) — DDB CRUD for MCPSERVER +
  MCPTOKEN rows; chat-scope override resolution.
- `src/channel/mcp/transports.py` (new) — `make_authenticated_transport`
  helper.
- `src/channel/api/mcp.py` (new) — REST surface (`/api/mcp/servers`
  CRUD + `/auth/mcp/callback`).
- `src/channel/agents/chat_agent.py` (modify) — `build_agent()` accepts
  MCP clients in `tools=[...]`; resolution helper.
- `ui/src/api.js` (modify) — MCP REST client.
- `ui/src/app/views/Customize.jsx` (modify) — "MCP servers" section
  (list, add, remove, toggle globally-enabled, reauth).
- `ui/src/app/AddMCPServerModal.jsx` (new) — register-server form +
  scope consent disclosure.
- `ui/src/app/MCPPicker.jsx` (new) — composer popover sibling of
  `ModelPicker` / `AttachMenu`.
- `ui/src/app/Composer.jsx` (modify) — wire the new popover.

### Sizing

`size:l` (a week+) — the load-bearing complexity is the auth-code flow
end-to-end (DCR + redirect-URI handling + token exchange + refresh).
Could decompose further (auth, REST, UI as three sub-issues) but that's
an implementation-plan decision for `#128-D`'s own writing-plans pass,
not a spike output.

### Does `#128-D` gate epic #128's close?

**No.** Per the strategy spec §"Closing condition" — epic #128 closes
when #181, #182, #183 merge and #184's design doc lands. `#128-D` (if
filed) ships under its own track and does not gate the epic close.
That's the right disposition for a `priority:p3` follow-up — it
shouldn't hold a quarterly release commitment.

## P5 sync-checkpoint with Stream A (#181)

Per the strategy spec policy P5, this spike's output is offered to
Stream A for a brief sync-check before #181's chassis closes. The
question for Stream A:

> Does this want `build_agent()` shaped differently? Does it want a
> different per-tool-provider hook scope?

The spike's answer (load-bearing for the sync):

- **`build_agent(tools=[...])` extends naturally to accept `MCPClient`
  instances alongside native tools.** `MCPClient` implements
  `ToolProvider`; Strands' flat list is polymorphic on the MCP axis.
  No chassis surgery required.
- **No change to `tool_hooks.py` scope.** MCP-tool calls flow through
  the same `BeforeToolCallEvent` / `AfterToolCallEvent` as native tools.
- **No new hook events.** Auth failures surface via the existing
  `sse_tool_error` with `error_type: "auth_expired"`.

The most likely Stream A response: "no chassis surgery; proceed as
planned." If Stream A surfaces a small signature tweak (e.g.
`build_agent(tools=..., mcp_clients=...)` as a separate list for
lifecycle-management reasons), that's a small-and-roll-in outcome per
P5. A large-and-defer outcome would require concrete evidence that a
flat list mishandles MCP — none of which surfaced during this spike's
read of `mcp_client.py`.

## Cross-references

- Issue: [#184]
- Parent epic: [#128] + [2026-06-03 decisions comment][decisions]
- Strategy spec: [`2026-06-06-epic-128-tool-use-sequencing.md`](./2026-06-06-epic-128-tool-use-sequencing.md)
- Chassis sub-issue: [#181] (P5 sync-checkpoint above)
- Channel-the-product 2026-06-05 input: posted on [#184][184-channel-input]
- Strands `MCPClient`:
  `strands/tools/mcp/mcp_client.py:104` (`class MCPClient(ToolProvider)`),
  `:116-138` (`__init__` signature),
  `:118` (`transport_callable: Callable[[], MCPTransport]`),
  `:232` (`load_tools`),
  `:772` (`_async_background_thread`),
  `:867` (`_map_mcp_content_to_tool_result_content`).
- Product decisions cited (CLAUDE.md `## Product decisions`):
  - "Workspaces are the tenancy root" — Q4 migration path
  - "Agents swap tokens to switch context" — future workspace-level token model
  - "Tool integrations use Strands' native `BedrockModel` + `MCPClient` + tool hooks" — verified by this spike against Strands 1.41.0
  - "Client-side LLM preferred" governs MCP **Sampling**, NOT MCP-as-tool-server — explicit callout above
- [ADR-0008] — push discipline (governs the spike-doc PR's push workflow)
- Hive (`https://hive.warlordofmars.net/mcp`) — designated test server;
  FastMCP + DCR + OAuth 2.1; live today.

[#128]: https://github.com/warlordofmars/channel/issues/128
[#181]: https://github.com/warlordofmars/channel/issues/181
[#184]: https://github.com/warlordofmars/channel/issues/184
[decisions]: https://github.com/warlordofmars/channel/issues/128#issuecomment-4617554628
[184-channel-input]: https://github.com/warlordofmars/channel/issues/184#issuecomment-0
[ADR-0008]: ../../adr/0008-push-discipline.md
