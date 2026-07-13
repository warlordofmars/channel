// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Navigate, Outlet } from "react-router-dom";
import { parseToken, TOKEN_KEY } from "../../lib/auth.js";

/**
 * Role gate for every `/app/admin/*` route. Reads the mgmt JWT from
 * localStorage via `parseToken` (CLAUDE.md "User identity from JWT")
 * and refuses non-admins with a `Navigate replace` back to `/app`.
 *
 * The check runs synchronously during render — no effect, no loading
 * state — so a non-admin never sees a flash of admin chrome, and the
 * redirect replaces the history entry so Back doesn't bounce the user
 * into the gate again.
 *
 * All admin child routes nest under this one element route in App.jsx
 * so the gating logic runs exactly once (issue #237). Client-side
 * gating is UX only — the real enforcement is the `require_admin`
 * dependency on the `/api/admin/*` endpoints (epic #233).
 */
export default function AdminLayout() {
  const claims = parseToken(localStorage.getItem(TOKEN_KEY) ?? "") ?? {};
  if (claims.role !== "admin") return <Navigate to="/app" replace />;
  return <Outlet />;
}
