# Channel

<!-- Backend stack -->
[![Python](https://img.shields.io/badge/python-3.12%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.135%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![AWS Lambda](https://img.shields.io/badge/AWS_Lambda-FF9900?logo=awslambda&logoColor=white)](https://aws.amazon.com/lambda/)
[![DynamoDB](https://img.shields.io/badge/DynamoDB-4053D6?logo=amazondynamodb&logoColor=white)](https://aws.amazon.com/dynamodb/)
[![AWS CDK](https://img.shields.io/badge/CDK-Python-FF9900?logo=amazonaws&logoColor=white)](https://aws.amazon.com/cdk/)

<!-- Frontend stack -->
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)](https://reactjs.org/)
[![Vite](https://img.shields.io/badge/Vite-5.x-646CFF?logo=vite&logoColor=white)](https://vitejs.dev/)
[![Node](https://img.shields.io/badge/Node-20-339933?logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/type--checked-mypy-blue)](https://mypy-lang.org/)

**The workspace for thinking with AI.**

Channel is an AI agent chat product — a web app and a native desktop app for
macOS, Windows, and Linux, backed by an AWS-native serverless stack. Chats
stream token-by-token from Amazon Bedrock, carry context across conversations
with Bedrock AgentCore Memory, and reach for tools mid-answer: web search,
sandboxed code execution, and any MCP server you register. Free to start.

<!--
  Product screenshot — asset capture is a follow-up (see #247 "Out of scope").
  Add docs/assets/screenshot.png, then replace this comment with:
  <p align="center">
    <img src="docs/assets/screenshot.png" alt="Channel — AI agent chat on web and desktop" width="820" />
  </p>
-->

**[channel.warlordofmars.net](https://channel.warlordofmars.net)** —
the product ·
**[Download](https://channel.warlordofmars.net/download)** ·
**[Docs](https://channel.warlordofmars.net/docs/getting-started/quick-start)** ·
**[Latest release](https://github.com/warlordofmars/channel/releases/latest)**

## What Channel does

- **Streaming chat** — token-by-token Bedrock responses over SSE, with
  auto-titling of new chats, follow-up suggestions, and attachments + vision.
- **Memory** — every turn is persisted to Bedrock AgentCore Memory, and a
  recall hook injects relevant context from prior chats into new conversations.
- **Tool use** — a tool-calling chassis with Exa web search, sandboxed Lambda
  code execution, and a registry of MCP servers (GitHub featured first-party),
  so agents can act, not just answer.
- **Web and desktop from one source** — the React SPA ships as both the web
  app and the Electron desktop app (signed, notarized, and auto-updating).

## What's inside

Channel is one repository with four shipping surfaces:

- **Backend** (`src/channel/`) — FastAPI management API on AWS Lambda (Function
  URLs), DynamoDB single-table storage, and Google OAuth login that mints
  management JWTs.
- **Web SPA** (`ui/`) — React 18 + Vite, a custom OKLCH design-token system (no
  Tailwind), serving both the marketing site and the chat app from one source.
- **Desktop app** (`desktop/`) — an Electron wrapper around the SPA with
  external-browser + loopback OAuth and auto-update.
- **Docs site** (`docs-site/`) — VitePress, served at `/docs/`.

Everything is provisioned with AWS CDK (Python) under `infra/`, deployed by
GitHub Actions via OIDC (no long-lived AWS keys), and held to 100% test
coverage (pytest + vitest).

## Architecture

```text
Browser · Desktop app · API client
                 │
                 ▼
┌───────────────────────────────────────────────┐
│                   CloudFront                    │
│                                                 │
│  /api · /auth · /oauth  → API Lambda (FastAPI)  │
│  /                      → S3 (SPA + marketing)  │
│  /docs/                 → S3 (VitePress docs)    │
└───────────────────────────────────────────────┘
                 │
      ┌──────────┴───────────┐
      ▼                      ▼
┌───────────┐     ┌───────────────────────┐
│ DynamoDB  │     │ Bedrock + AgentCore   │
│ (single   │     │ Memory (chat + recall)│
│  table)   │     └───────────────────────┘
└───────────┘
```

## Quick start

Full setup — AWS prerequisites, Google OAuth, and the first deploy — lives in
the docs so it stays in one place:

**→ [Quick start](https://channel.warlordofmars.net/docs/getting-started/quick-start)**

To run the whole stack locally (DynamoDB Local + API + Vite dev server):

```bash
uv sync --all-extras       # Python deps (requires uv)
cd ui && npm install       # JS deps
uv run inv dev             # DynamoDB Local, API, and the Vite dev server
```

`inv dev` blocks; in a second terminal, provision the (ephemeral) local table
schema once it's up:

```bash
uv run python scripts/reset_dev_table.py
```

Then open the local URL `inv dev` prints. For the desktop app, `uv run inv desktop-dev`.

## Contributing

```bash
git clone https://github.com/warlordofmars/channel
uv sync --all-extras       # Python deps (requires uv)
cd ui && npm install       # JS deps
uv run inv pre-push        # lint + type check + unit tests + frontend tests
```

Channel uses a dual-branch model: `development` is the default branch (feature
and fix PRs land there via squash merge) and `main` is release-only
(`release/vX.Y.Z` PRs land via merge commit, after which CI back-merges to
`development`). Both branches are protected behind the required CI status checks
in [`.github/workflows/ci.yml`](.github/workflows/ci.yml). See
[CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

## Security

To report a vulnerability, use GitHub's private vulnerability reporting rather
than opening a public issue. See [SECURITY.md](SECURITY.md) for the full
disclosure policy.

## History

Channel began as an AWS-native agent-backend starter template and has since
grown into a full product. That lineage survives only in the shape of the CDK
stack and the CI wiring; the chat experience, memory, tool use, desktop app,
and docs site are all Channel.
