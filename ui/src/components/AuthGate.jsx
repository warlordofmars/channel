// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { Navigate } from "react-router-dom";
import { TOKEN_KEY, isTokenValid } from "../lib/auth.js";

/**
 * Wraps any /app/* route. Reads the mgmt JWT from localStorage and either
 * renders the children or redirects to /app/login.
 *
 * NB: this is a render-time gate only. The mgmt JWT itself is verified by
 * the backend on every /api/* request; the frontend gate is UX, not
 * security.
 */
export default function AuthGate({ children }) {
  const token = localStorage.getItem(TOKEN_KEY) ?? "";
  if (!isTokenValid(token)) {
    return <Navigate to="/app/login" replace />;
  }
  return children;
}
