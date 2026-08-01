# Copilot review instructions for Channel

Channel is an AI agent chat backend on AWS (FastAPI + React + DynamoDB on AWS Lambda). Use the rules below when reviewing pull requests in this repo so the autonomous agent can act on your feedback cleanly.

## What to flag

- **Correctness** — logic errors the test suite wouldn't catch: wrong auth scope, race conditions on DynamoDB writes, mis-handled Pydantic models, off-by-one in pagination, missing timezone handling.
- **Security** — hardcoded secrets, SQL-ish injection (DynamoDB filter expressions are our analog), overly-broad OAuth scopes, missing auth on new endpoints, PII leaking into logs.
- **Clarity** — ambiguous naming, unreachable branches, missing invariants, API responses that would surprise a caller.
- **Missing tests** — new Python module without `tests/unit/` coverage; new React component without a co-located `*.test.jsx`.

## What NOT to flag

The agent will auto-dismiss these as style-only nits and move on.

- **Class-name ordering** — the project doesn't enforce a class-sorting convention (and doesn't use Tailwind at all).
- **`const` vs `let`** — both are fine; don't suggest re-writes.
- **Import order** inside Python or JS modules — `ruff` handles Python; the JS side isn't linted on import order.
- **Naming preferences** that don't clarify intent (e.g. `handleClick` vs `onClick`).
- **"Prefer early return"** style rewrites when the current code is already clear.

## Project conventions (summary)

- **Python deps**: `uv` only. Never `pip` or `requirements.txt`.
- **Lint / type**: `ruff` + `mypy`, strict mode. Both run in CI and must pass.
- **Tests**: 100% coverage enforced (`pytest --cov-fail-under=100` for Python, vitest v8 threshold for JS). CI fails below.
- **UI components**: Every `.jsx` component has a co-located `.test.jsx`.
- **UI styling**: Use CSS variables (`var(--ink-soft)`, `var(--border)`, `var(--accent)`) defined in `ui/src/styles/channel.css`; never hardcode hex colours. Icons come from the hand-rolled `ui/src/components/Icon.jsx` stroke set (`<Icon name="search" size={18} />`) — never emojis as UI elements, and never an icon package (the project ships none).
- **UI primitives**: Hand-rolled, no component library. Reusable primitives are plain `.jsx` files directly under `ui/src/components/`; there is no Tailwind, no `shadcn/ui`, and no `ui/src/components/ui/` layer, so raw HTML elements with semantic class names are the correct shape — don't suggest a component library.
- **Copyright headers**: Every new source file carries `// Copyright (c) 2026 John Carter. All rights reserved.` (or `#` for Python).
- **No new `README.md` / `docs/*.md` files** unless the issue explicitly asks for them — this repo dislikes drive-by documentation.
- **Auth**: OAuth 2.1 + PKCE for API clients, Google OAuth for mgmt UI. Any changes near `src/channel/auth/` warrant extra scrutiny.

## Agent-specific rule

If you spot a change to `CLAUDE.md §Autonomous issue workflow` or `§Stop and ask`, **flag it**. Those sections must go through human review — the autonomous agent is not allowed to auto-merge changes to its own operating rules. The agent already knows this; if it slipped through, that's a regression the human reviewer needs to see.

## Tone

Short, specific, actionable. One suggestion per comment when possible — the agent triages comments individually, and a comment bundling three findings is harder to act on than three separate ones.
