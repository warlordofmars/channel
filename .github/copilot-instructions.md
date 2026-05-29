# Copilot review instructions for Channel

Channel is a production-ready starter template for AWS-native AI agent backend services (FastAPI + React + DynamoDB on AWS Lambda). Use the rules below when reviewing pull requests in this repo so the autonomous agent can act on your feedback cleanly.

## What to flag

- **Correctness** — logic errors the test suite wouldn't catch: wrong auth scope, race conditions on DynamoDB writes, mis-handled Pydantic models, off-by-one in pagination, missing timezone handling.
- **Security** — hardcoded secrets, SQL-ish injection (DynamoDB filter expressions are our analog), overly-broad OAuth scopes, missing auth on new endpoints, PII leaking into logs.
- **Clarity** — ambiguous naming, unreachable branches, missing invariants, API responses that would surprise a caller.
- **Missing tests** — new Python module without `tests/unit/` coverage; new React component without a co-located `*.test.jsx`.

## What NOT to flag

The agent will auto-dismiss these as style-only nits and move on.

- **Tailwind class order** — the project doesn't enforce a class-sorting convention.
- **`const` vs `let`** — both are fine; don't suggest re-writes.
- **Import order** inside Python or JS modules — `ruff` handles Python; the JS side isn't linted on import order.
- **Naming preferences** that don't clarify intent (e.g. `handleClick` vs `onClick`).
- **"Prefer early return"** style rewrites when the current code is already clear.

## Project conventions (summary)

- **Python deps**: `uv` only. Never `pip` or `requirements.txt`.
- **Lint / type**: `ruff` + `mypy`, strict mode. Both run in CI and must pass.
- **Tests**: 100% coverage enforced (`pytest --cov-fail-under=100` for Python, vitest v8 threshold for JS). CI fails below.
- **UI components**: Every `.jsx` component has a co-located `.test.jsx`.
- **UI styling**: Use CSS variables (`var(--text-muted)`, `var(--border)`, `var(--accent)`); never hardcode hex colours. Use `lucide-react` icons — never emojis as UI elements. Prefer `shadcn/ui` primitives (`Button`, `Card`, `Table`) over raw HTML.
- **Copyright headers**: Every new source file carries `// Copyright (c) 2026 John Carter. All rights reserved.` (or `#` for Python).
- **No new `README.md` / `docs/*.md` files** unless the issue explicitly asks for them — this repo dislikes drive-by documentation.
- **Auth**: OAuth 2.1 + PKCE for API clients, Google OAuth for mgmt UI. Any changes near `src/channel/auth/` warrant extra scrutiny.

## Agent-specific rule

If you spot a change to `CLAUDE.md §Autonomous issue workflow` or `§Stop and ask`, **flag it**. Those sections must go through human review — the autonomous agent is not allowed to auto-merge changes to its own operating rules. The agent already knows this; if it slipped through, that's a regression the human reviewer needs to see.

## Tone

Short, specific, actionable. One suggestion per comment when possible — the agent triages comments individually, and a comment bundling three findings is harder to act on than three separate ones.
