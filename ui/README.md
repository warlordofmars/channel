# AgentCore Starter UI

React 18 + Vite management SPA. Runs at `http://localhost:5173` in development and is served from CloudFront in production.

## Features

- **Users** — admin user management panel
- **Dashboard** — admin CloudWatch metrics and cost overview

## Development setup

```bash
cd ui
npm install
npm run dev        # http://localhost:5173
```

The UI talks to the management API at `VITE_API_BASE` (empty by default, so it uses relative paths). When running locally against the deployed API:

```bash
VITE_API_BASE=https://<api-lambda-url> npm run dev
```

Or run the API locally:

```bash
# In another terminal
cd ..
STARTER_JWT_SECRET=dev-secret uv run uvicorn starter.api.main:app --port 8001 --reload

# Then
VITE_API_BASE=http://localhost:8001 npm run dev
```

## Authentication

The UI stores the management Bearer token in `localStorage` under the key `starter_mgmt_token`.

## Project structure

```
ui/src/
├── main.jsx                      # React entry point
├── App.jsx                       # Root component, tab navigation
├── api.js                        # Thin fetch wrapper (reads token from localStorage)
├── analytics.js                  # GA4 helpers
├── setupTests.js                 # vitest + @testing-library setup
├── hooks/
│   └── useTheme.js               # Dark/light theme hook
├── lib/
│   ├── consent.js                # GA consent management
│   ├── limits.js                 # formatBytes utility
│   └── utils.js                  # cn and other shared utils
└── components/
    ├── LoginPage.jsx             # Google OAuth login page
    ├── Dashboard.jsx             # Admin: CloudWatch metrics + cost data
    ├── UsersPanel.jsx            # Admin: user list + management
    ├── EmptyState.jsx            # Shared empty-state illustration
    └── PageLayout.jsx            # Shared marketing page layout + navbar
```

## Available scripts

| Command | Description |
|---|---|
| `npm run dev` | Start Vite dev server with HMR |
| `npm run build` | Production build to `dist/` |
| `npm run preview` | Preview production build locally |
| `npm test` | Run vitest (single pass) |
| `npm run test:watch` | Run vitest in watch mode |
| `npm run lint` | ESLint |

## Building for production

```bash
npm run build
```

Output goes to `ui/dist/`. The CDK stack's `BucketDeployment` construct picks up this directory and uploads it to S3 as part of `cdk deploy`.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `VITE_API_BASE` | `""` (relative) | Base URL for the management API |

In production, the API is served from the same CloudFront domain under `/api/*` and `/oauth/*`, so `VITE_API_BASE` stays empty and all requests use relative paths.
