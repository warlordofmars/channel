# Channel UI

React 18 + Vite SPA. Runs at `http://localhost:5173` in development and is served from CloudFront in production. The same source builds both the web SPA and the renderer bundled by the Electron wrapper in `desktop/`.

## Features

- **Marketing site** (`/`, `/product`, `/models`, `/pricing`, `/download`, `/about`, `/blog`, `/careers`, `/privacy`) — public pages
- **Chat app** (`/app/*`) — conversation view, composer, projects, artifacts, and the customize panel, behind the Google-OAuth login at `/app/login`
- **Admin** (`/app/admin/*`) — user list/detail plus the CloudWatch metrics dashboard; admin-role JWTs only

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
CHANNEL_JWT_SECRET=dev-secret uv run uvicorn channel.api.main:app --port 8001 --reload

# Then
VITE_API_BASE=http://localhost:8001 npm run dev
```

For the full local stack (DynamoDB Local + API + Vite in one process tree) prefer `uv run inv dev` from the repo root — see CLAUDE.md §"Running the full stack locally". The bare `uvicorn` command above starts the API alone, which is enough for UI work that doesn't hit DynamoDB.

## Authentication

The UI stores the management Bearer token in `localStorage` under the key `starter_mgmt_token`.

## Project structure

Every component has a co-located `*.test.jsx`; they are omitted below for brevity.

```
ui/src/
├── main.jsx                      # React entry point
├── App.jsx                       # Route table (marketing + /app/* + /app/admin/*)
├── api.js                        # Thin fetch wrapper (reads token from localStorage)
├── analytics.js                  # GA4 helpers
├── setupTests.js                 # vitest + @testing-library setup
├── styles/
│   ├── channel.css               # Design tokens (OKLCH themes, radii, shadows, fonts)
│   ├── site.css                  # Marketing site layout
│   └── app.css                   # Chat-app layout
├── hooks/
│   ├── ChatsContext.jsx          # ChatsProvider + useChats() — one useChatList app-wide
│   ├── useAssetContent.js        # Auth-fetched asset payload → object-URL or text
│   ├── useChannelPrefs.js        # theme/accent/density/shape/font/model/effort
│   ├── useChatList.js            # Sidebar Recents + optimistic create/rename/archive
│   └── useChatStream.js          # SSE chat stream: history, send, abort, status
├── lib/
│   ├── auth.js                   # parseToken, isTokenValid, TOKEN_KEY
│   ├── consent.js                # GA consent management
│   ├── limits.js                 # formatBytes utility
│   ├── sseParser.js              # SSE frame parser
│   └── utils.js                  # cn and other shared utils
├── components/                   # Shared primitives (hand-rolled, no component library)
│   ├── AuthGate.jsx              # Redirects /app/* visits to /app/login when no JWT
│   ├── ChannelMark.jsx           # Brand mark SVG
│   ├── ErrorBoundary.jsx         # Token-styled error fallback
│   ├── Icon.jsx                  # 24×24 stroke icon set
│   └── Modal.jsx                 # Backdrop + Esc dismissal primitive
├── marketing/                    # Nav, Footer, SiteLayout, ThemeToggle, ImageSlot
│   └── pages/                    # Home, Product, Models, Pricing, Download, …
└── app/                          # Chat app — Shell, Sidebar, Composer, Conversation, …
    ├── admin/                    # AdminHome, AdminLayout, Users, UserDetail, Dashboard
    └── views/                    # Projects, ProjectDetail, Artifacts, ArtifactPanel, Customize
```

## Available scripts

| Command | Description |
|---|---|
| `npm run dev` | Start Vite dev server with HMR |
| `npm run build` | Production build to `dist/` |
| `npm run preview` | Preview production build locally |
| `npm test` | Run vitest (single pass) |
| `npm run test:coverage` | Run vitest with the v8 100% coverage gate (what CI runs) |
| `npm run test:watch` | Run vitest in watch mode |
| `npm run lint` | ESLint |

`uv run inv pre-push` runs `npm test`, **not** `npm run test:coverage` — so a per-file coverage gap passes the local gate and fails CI. Run `npm run test:coverage` before pushing a UI change.

## Building for production

```bash
npm run build
```

Output goes to `ui/dist/`. The CDK stack's `BucketDeployment` construct picks up this directory and uploads it to S3 as part of `cdk deploy`.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `VITE_API_BASE` | `""` (relative) | Base URL for the management API |

In production, the API is served from the same CloudFront domain under `/api/*`, `/auth/*` and `/health`, so `VITE_API_BASE` stays empty and all requests use relative paths. In development the Vite proxy (`ui/vite.config.js`) forwards `/api`, `/auth` and `/oauth` to the API on port 8001 and `/mcp` to port 8000.
