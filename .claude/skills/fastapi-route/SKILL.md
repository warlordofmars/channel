---
name: fastapi-route
description: Conventions for adding a new FastAPI endpoint to the management API — file location, router wiring, auth dependency, streaming pattern, test layout, coverage gate.
status: full
triggers:
  paths:
    - "src/channel/api/**.py"
  areas:
    - "api"
---

# fastapi-route

Adding a FastAPI endpoint to the Channel management API follows
seven conventions. Each is mechanically checkable against the
existing `src/channel/api/` code; the canonical examples are cited
inline by file.

## 1. File location

Endpoints live in `src/channel/api/<area>.py` where `<area>`
matches the issue's area label. The current router files include:

- `src/channel/api/chats.py` — chat CRUD + SSE streaming + regenerate
- `src/channel/api/models.py` — `GET /api/models` server allowlist
- `src/channel/api/assets.py` — per-chat asset list/get/content/delete
- `src/channel/api/attachments.py` — upload endpoints
- `src/channel/api/prefs.py` — per-user UI preferences
- `src/channel/api/mcp.py` — MCP server registry (mounted only when
  `STARTER_MCP_REGISTRY_ENABLED=1`, the default)
- `src/channel/api/admin.py` — admin-only metrics
- `src/channel/api/csp.py` — CSP violation report receiver
- `src/channel/api/main.py` — app construction, middleware,
  `/health`. Do **not** add domain endpoints here; route handlers
  go in dedicated router files.

A new functional area (e.g. `users`, `widgets`) → new file
`src/channel/api/<area>.py` with its own `APIRouter`. Do not
extend an existing router with unrelated routes — area-per-file is
the discoverability contract. The fully-worked endpoint module
template lives at [`example.py`](./example.py) — copy it and adapt
the names.

## 2. Router wiring in `main.py`

Every router file is included from `src/channel/api/main.py` with
`app.include_router(...)`. The registrations follow this shape:

```python
# Management UI auth endpoints (unauthenticated — issues mgmt JWTs)
app.include_router(mgmt_auth_router)

# CSP report receiver — unauthenticated by design
app.include_router(csp_router, prefix="/api")

# Chat CRUD + SSE streaming (requires mgmt JWT)
app.include_router(chats_router, prefix="/api")
```

Conventions:

- Authenticated domain routers carry `prefix="/api"`.
- Auth routers do **not** carry `/api` (they expose well-known
  paths like `/auth/login` and `/auth/callback`).
- Add a one-line comment above each `include_router` call stating
  the auth posture (unauthenticated / requires mgmt JWT / etc.).
- Import the router as `<area>_router` (alias on import) to keep
  the registration block readable.

## 3. Auth dependency pattern

Two auth dependencies are exported from
`src/channel/api/_auth.py`:

- `require_mgmt_user` (`src/channel/api/_auth.py`) — validate a
  management JWT issued by the Google OAuth login flow. Returns
  the JWT claims dict. Use for any endpoint hit by the management
  UI or by an authenticated user agent.
- `require_admin` (`src/channel/api/_auth.py`) — composes
  `require_mgmt_user` and additionally requires `role == "admin"`.
  Use for admin-only endpoints (user management, dashboard).

Wire either one through `Depends(...)`. The canonical shape is in
[`example.py`](./example.py) — the non-streaming and streaming
handlers:

```python
from typing import Any
from fastapi import Depends
from channel.api._auth import require_mgmt_user

@router.post("/widgets")
def create_widget(
    body: WidgetCreateRequest,
    claims: dict[str, Any] = Depends(require_mgmt_user),
) -> WidgetResponse:
    ...
```

Naming convention: bind the claims to `_claims` (leading
underscore) when the handler does not consume them, and to
`claims` when it does — for example `claims["sub"]` for the
caller's user id.

Every authenticated endpoint uses `require_mgmt_user` or
`require_admin`.

## 4. Streaming pattern

Streaming endpoints return
`StreamingResponse(generator(), media_type="text/event-stream")`.
The canonical example is the streaming handler in
[`example.py`](./example.py):

```python
@router.post("/widgets/stream")
def stream_widgets(
    _claims: dict[str, Any] = Depends(require_mgmt_user),
) -> StreamingResponse:
    def _stream() -> Iterator[str]:
        yield from stream_agent_reply(...)  # delegate to a domain generator

    return StreamingResponse(_stream(), media_type="text/event-stream")
```

The SSE event schema is fixed by ADR-0002 §Decision #4:

- `data: {"type": "delta", "text": "..."}` — incremental token
- `data: {"type": "done", ...}` — final event with
  metadata (`stop_reason`, token counts, `session_id`, etc.)

Each event terminates with `\n\n`. In Channel the streaming
generator lives in `src/channel/api/chats.py`, which drives a
Strands agent and delegates per-event wire formatting to
`translate_event` (emitting `sse_delta` / `sse_done` frames) in
`src/channel/agents/strands_sse.py`. The route handler's only job
is to delegate to that generator and wrap it in
`StreamingResponse`.

Lambda streaming caveat: in production the function runs behind
AWS Lambda Web Adapter with `AWS_LWA_INVOKE_MODE=response_stream`
(see ADR-0002). CloudFront may buffer SSE; for low-latency
streaming clients should connect to the Function URL directly.

## 5. Test structure

Every new endpoint requires unit tests. Tests live alongside the
existing suites under `tests/unit/test_<area>_api.py`. The
companion test cases for the worked example are listed at the
bottom of [`example.py`](./example.py); the pattern is:

- Construct a `TestClient(app)` once at module scope.
- Set `STARTER_JWT_SECRET` via `os.environ.setdefault` *before*
  importing the app, so JWT issuance/validation works.
- Provide an `_auth_headers()` helper that issues a real
  management JWT via `issue_mgmt_jwt(...)` — do not mock the auth
  dependency itself; mock the AWS boundary (`boto3` clients,
  `converse`, `invoke`) instead.
- Cover, at minimum:
  1. **Auth required** — call without headers, assert 401 or 403.
  2. **Happy path** — patch the AWS boundary, assert response
     body shape and forwarded fields.
  3. **Argument forwarding** — capture the call to the mocked
     boundary and assert the request payload was constructed
     correctly (system prompt, session id, user id from claims).

For streaming endpoints add two more:

- **Content type** — assert
  `"text/event-stream" in resp.headers["content-type"]`.
- **Event sequence** — split the response body on `\n\n`, parse
  each `data: ` payload as JSON, assert the schema (`type` field
  is `delta` then `done`).

If the endpoint reads or writes DynamoDB, add an integration test
under `tests/integration/test_<area>_api.py` that exercises the
real DynamoDB Local instance via the existing `conftest.py`
fixtures.

## 6. 100% coverage gate

CI fails below 100% line coverage. Two pitfalls recur in API
code:

- **Anonymous functions inside `StreamingResponse`** — vitest-style
  v8 counters do not apply on the Python side, but the inner
  `_stream` closure does need at least one test that drives it to
  completion (split the response body on `\n\n` and assert the
  parsed event sequence — see the streaming handler in
  [`example.py`](./example.py) for the shape).
- **Untested error branches** — every `raise HTTPException(...)`
  needs a test that triggers it, even for trivial validation
  paths. If a branch is genuinely unreachable, mark the line with
  `# pragma: no cover` and a one-line comment explaining why; do
  not lower the gate.

Run the gate locally before opening a PR:

```bash
uv run inv pre-push
```

This runs lint + typecheck + unit tests + frontend tests with the
same coverage threshold CI enforces.

## 7. Copyright header

Every new Python file starts with the copyright header from
CLAUDE.md §Copyright headers:

```python
# Copyright (c) 2026 John Carter. All rights reserved.
```

When editing an existing file in a new calendar year, append the
year to the existing line — do not duplicate the header.

The `scripts/check_copyright.py` linter runs in `inv pre-push`
and CI; a missing or malformed header fails the build.

## See also

- [`example.py`](./example.py) — a complete copy-pasteable
  endpoint module covering all seven conventions.
- ADR-0002 §Decision #4 — SSE event schema authority
  (`docs/adr/0002-streaming-lambda-web-adapter.md`).
- CLAUDE.md §Auth — auth posture across the codebase.
- CLAUDE.md §Testing — coverage gate, fixture conventions.
