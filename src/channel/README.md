# `starter` package

Module index for `src/starter/`. The management API and agent wrappers
live here.

For project-wide architecture, conventions, and the data model,
see the top-level [`CLAUDE.md`](../../CLAUDE.md) and
[`README.md`](../../README.md).

## Layout

```text
src/starter/
├── logging_config.py    # Structured JSON logging setup
├── metrics.py           # CloudWatch EMF metrics helpers
├── auth/
│   ├── tokens.py        # Management JWT issuance and validation
│   ├── google.py        # Google OAuth integration (management UI login)
│   └── mgmt_auth.py     # Management UI auth routes (/auth/login, /auth/callback)
├── agents/
│   ├── chat_agent.py    # Strands Agent factory (build_agent / resolve_model_id)
│   └── strands_sse.py   # Strands event → SSE byte translator
└── api/
    ├── main.py          # FastAPI app wiring (CORS, middleware, routers, /health)
    ├── _auth.py         # require_mgmt_user / require_admin FastAPI dependencies
    └── csp.py           # CSP violation reporting endpoint
```
