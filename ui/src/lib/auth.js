// Copyright (c) 2026 John Carter. All rights reserved.

export const TOKEN_KEY = "starter_mgmt_token";

export function parseToken(token) {
  if (!token) return null;
  try {
    return JSON.parse(atob(token.split(".")[1].replaceAll("-", "+").replaceAll("_", "/")));
  } catch {
    return null;
  }
}

export function isTokenValid(token) {
  const payload = parseToken(token);
  return payload ? payload.exp * 1000 > Date.now() : false;
}
