# `channel` package

Module index for `src/channel/`. The management API, auth, agents, and
MCP integration live here.

For project-wide architecture, conventions, and the data model,
see the top-level [`CLAUDE.md`](../../CLAUDE.md) and
[`README.md`](../../README.md).

## Layout

```text
src/channel/
├── storage.py            # DynamoDB persistence for chats, messages, assets, tokens
├── models.py             # Pydantic wire-format models for the chat domain
├── _table_schema.py      # Shared table schema for local-dev / integration provisioning
├── startup.py            # Startup-time validation of SSM-backed configuration
├── logging_config.py     # Structured JSON logging setup
├── metrics.py            # CloudWatch EMF metrics helpers
├── auth/
│   ├── tokens.py         # Management JWT issuance and validation
│   ├── google.py         # Google OAuth integration (management UI login)
│   ├── mgmt_auth.py      # Management UI auth routes (/auth/login, /auth/callback)
│   ├── logout.py         # POST /auth/logout — mgmt-session revocation
│   └── state_store.py    # DynamoDB-backed OAuth state parameter store
├── agents/
│   ├── chat_agent.py      # Strands Agent factory (build_agent / build_titler_agent)
│   ├── memory.py          # AgentCore Memory write hook
│   ├── recall.py          # AgentCore Memory recall hook
│   ├── tool_hooks.py      # Tool-use lifecycle hooks (telemetry, META facts)
│   ├── asset_producers.py # Everything that creates ASSET rows on the stream path
│   ├── strands_sse.py     # Strands event → SSE byte translator
│   └── tools/             # Native Strands @tools
│       ├── clock.py            # current_time
│       ├── code_exec.py        # Sandboxed Python execution
│       ├── generate_image.py   # Stability Stable Image Core generation
│       ├── memory_tools.py     # remember / recall
│       ├── web_fetch.py        # URL → content
│       └── web_search.py       # Exa-backed search
├── api/
│   ├── main.py           # FastAPI app wiring (CORS, middleware, routers, /health)
│   ├── _auth.py          # require_mgmt_user / require_admin FastAPI dependencies
│   ├── _debug.py         # Dev-only debug routes (CHANNEL_ENABLE_DEBUG_ENDPOINTS=1)
│   ├── chats.py          # Chat CRUD + SSE streaming + regenerate
│   ├── assets.py         # Per-chat asset list/get/content/delete + browse
│   ├── attachments.py    # Presigned S3 upload + finalize
│   ├── mcp.py            # MCP-server registry REST + OAuth callback
│   ├── models.py         # GET /api/models — server allowlist
│   ├── prefs.py          # GET/PUT /api/me/prefs
│   ├── admin.py          # Admin-only user list/detail + metrics
│   └── csp.py            # CSP violation reporting endpoint
├── mcp/
│   ├── auth.py           # OAuth 2.1 + DCR helpers for the MCP-server lifecycle
│   ├── crypto.py         # KMS encrypt/decrypt for MCP token blobs
│   ├── featured.py       # Curated first-party MCP server catalog
│   ├── transports.py     # Authenticated MCP transport thunk
│   └── url_guard.py      # SSRF guard for user-supplied MCP-server URLs
└── sandbox/
    └── handler.py        # Lambda handler for the code-execution sandbox
```
