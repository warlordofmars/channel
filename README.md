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

A production-ready starter template for building AWS-native AI agent backend services.

## What's included

- **FastAPI** management REST API with Google OAuth login
- **AWS Lambda** + Function URL hosting
- **DynamoDB** single-table storage
- **CloudFront** + S3 CDN for the management UI
- **React** management SPA (Vite + shadcn/ui)
- **AWS CDK** (Python) infrastructure as code
- **OAuth 2.1** authorization server with PKCE
- **GitHub Actions** CI/CD with OIDC (no long-lived AWS keys)
- **100% test coverage** enforced (pytest + vitest)

## Architecture

```
Browser / API client
      │
      ▼
┌──────────────────────────────────────────────┐
│                  CloudFront                   │
│                                               │
│  /api/* /oauth/*  → API Lambda (FastAPI)      │
│  /                → S3 (React SPA)            │
│  /docs/           → S3 (VitePress docs)       │
└──────────────────────────────────────────────┘
              │
              ▼
       ┌─────────────┐
       │  DynamoDB   │
       └─────────────┘
```

| Layer | Technology |
|---|---|
| Auth server | OAuth 2.1 + PKCE (self-contained, built into API Lambda) |
| Management API | FastAPI (Python) |
| Management UI | React 18 + Vite |
| Storage | DynamoDB (single-table design) |
| Hosting | AWS Lambda Function URLs + CloudFront + S3 |
| IaC | AWS CDK (Python) |
| CI/CD | GitHub Actions + OIDC |

## Getting started

See [docs-site/getting-started/quick-start.md](docs-site/getting-started/quick-start.md) or run:

```bash
# Install dependencies
uv sync

# Bootstrap CDK (first time only)
cd infra && cdk bootstrap -c account=YOUR_ACCOUNT_ID -c env=dev

# Deploy to dev
uv run inv deploy --env dev
```

## Local development

```bash
# Start all services (DynamoDB Local, API, Vite dev server)
uv run inv dev

# Seed demo data
uv run inv seed

# Open http://localhost:5173?test_email=you@example.com
```

## Development

This repo uses a dual-branch model: `development` is the GitHub default branch (where feature/fix PRs land via squash merge) and `main` is release-only (where `release/vX.Y.Z` PRs land via merge commit, then are auto back-merged to `development` by CI). Both branches are protected behind the seven required CI status checks defined in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (`Lint & Type Check`, `Unit Tests`, `Integration Tests (DynamoDB Local)`, `Frontend Tests & Build`, `Coverage Report`, `Infra Synth`, `Security Audit`) with `strict=true` so the head SHA must be up-to-date with the base branch at merge time. The repository has `allow_auto_merge=true` enabled, so `gh pr merge --auto --squash --delete-branch` queues the PR for merge as soon as required checks pass. The expected branch-protection snapshot is checked in at [`infra/branch-protection.expected.json`](infra/branch-protection.expected.json); fresh forks should apply Phase 1.5 of the onboarding agent (`.claude/agents/onboarding.md`) to install the same protections before opening any PR.

## Contributing

```bash
git clone <your-fork>
uv sync --all-extras    # install Python deps (requires uv)
cd ui && npm install    # install JS deps
uv run inv pre-push     # lint + type check + unit tests + frontend tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full dev workflow.

## Security

To report a vulnerability, use GitHub's private vulnerability reporting rather than opening a public issue. See [SECURITY.md](SECURITY.md) for the full disclosure policy.
