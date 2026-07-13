// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Icon from "../../components/Icon.jsx";
import { ApiError, getAdminUsers } from "../../api.js";

/**
 * Column set for the users table. `sort` names the server-side sort
 * field (#235) — direction is fixed per field (timestamps descending,
 * email ascending), so a header click switches the sort key rather
 * than flipping direction. Columns without `sort` render as plain
 * labels (`chat_count` / `last_login_at` are not server-sortable).
 */
const COLUMNS = [
  { key: "email", label: "Email", sort: "email" },
  { key: "created_at", label: "Joined", sort: "created_at" },
  { key: "last_chat_at", label: "Last chat", sort: "last_chat_at" },
  { key: "chat_count", label: "Chats" },
  { key: "last_login_at", label: "Last login" },
];

const HEADER_BUTTON_STYLE = {
  background: "none",
  border: "none",
  padding: 0,
  font: "inherit",
  color: "inherit",
  textTransform: "inherit",
  letterSpacing: "inherit",
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
};

const MUTED_STYLE = { fontSize: 13.5, color: "var(--ink-soft)" };

/**
 * Render an ISO-8601 timestamp as its date part, or an em dash for
 * null. `last_login_at` is always null today (no login-path
 * denormalization yet — #235) and `created_at` / `last_chat_at` are
 * nullable, so the dash branch is load-bearing, not decorative.
 * Slicing beats `toLocaleDateString` here: deterministic under any CI
 * locale and it matches the mono-font table aesthetic.
 */
export function fmtDate(iso) {
  return iso ? iso.slice(0, 10) : "—";
}

/**
 * `/app/admin/users` — paginated registered-user table (#238).
 *
 * Pagination follows the #235 cursor contract: page on `next_cursor`
 * (null = exhausted — never item count), and a cursor is only valid
 * under the sort it was issued with, so switching sort resets to page
 * 1 and a 400 on "Load more" (stale/foreign cursor) restarts from
 * page 1 rather than surfacing an error.
 */
export default function Users() {
  const [rows, setRows] = useState(null); // null = first page in flight
  const [nextCursor, setNextCursor] = useState(null);
  const [sort, setSort] = useState("last_chat_at");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false); // "Load more" in flight
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(
    function loadFirstPage() {
      let cancelled = false;
      setRows(null);
      setNextCursor(null);
      setError(null);
      getAdminUsers({ sort })
        .then(function onFirstPage(data) {
          if (cancelled) return;
          setRows(data.items);
          setNextCursor(data.next_cursor);
        })
        .catch(function onFirstPageError() {
          if (cancelled) return;
          setRows([]);
          setError("Could not load users.");
        });
      return function cancelFirstPage() {
        cancelled = true;
      };
    },
    [sort, reloadTick],
  );

  function handleHeaderClick(event) {
    const field = event.currentTarget.dataset.sort;
    // Clicking the active header is a no-op — direction is fixed
    // server-side, so there is nothing to flip.
    if (field !== sort) setSort(field);
  }

  function handleRetry() {
    setReloadTick(function bumpTick(t) {
      return t + 1;
    });
  }

  async function handleLoadMore() {
    setBusy(true);
    setError(null);
    try {
      const data = await getAdminUsers({ sort, cursor: nextCursor });
      setRows(function appendPage(prev) {
        return [...prev, ...data.items];
      });
      setNextCursor(data.next_cursor);
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        // Stale/foreign cursor (#235 contract) — restart from page 1.
        handleRetry();
      } else {
        setError("Could not load more users.");
      }
    } finally {
      setBusy(false);
    }
  }

  let content;
  if (rows === null) {
    content = <p style={MUTED_STYLE}>Loading users…</p>;
  } else if (error && rows.length === 0) {
    content = (
      <div>
        <p style={{ ...MUTED_STYLE, color: "var(--danger)" }}>{error}</p>
        <button type="button" className="btn-secondary" onClick={handleRetry}>
          Retry
        </button>
      </div>
    );
  } else if (rows.length === 0) {
    content = <p style={MUTED_STYLE}>No users yet.</p>;
  } else {
    content = (
      <div>
        <table className="art-table" data-testid="admin-users-table">
          <thead>
            <tr>
              {COLUMNS.map((col) => (
                <th key={col.key}>
                  {col.sort ? (
                    <button
                      type="button"
                      data-sort={col.sort}
                      onClick={handleHeaderClick}
                      aria-pressed={col.sort === sort}
                      style={HEADER_BUTTON_STYLE}
                    >
                      {col.label}
                      {col.sort === sort && (
                        <span
                          data-testid="sort-indicator"
                          style={{
                            display: "inline-flex",
                            // email sorts ascending; timestamps descending.
                            transform: sort === "email" ? "none" : "rotate(180deg)",
                          }}
                        >
                          <Icon name="arrow-up" size={11} />
                        </span>
                      )}
                    </button>
                  ) : (
                    col.label
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((u) => (
              <tr key={u.user_id}>
                <td>
                  <Link
                    to={`/app/admin/users/${encodeURIComponent(u.user_id)}`}
                    style={{
                      color: "var(--accent-ink)",
                      textDecoration: "none",
                      fontWeight: 600,
                    }}
                  >
                    {u.email}
                  </Link>
                </td>
                <td className="mono">{fmtDate(u.created_at)}</td>
                <td className="mono">{fmtDate(u.last_chat_at)}</td>
                <td className="mono">{u.chat_count}</td>
                <td className="mono">{fmtDate(u.last_login_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {error && (
          <p style={{ ...MUTED_STYLE, color: "var(--danger)", marginTop: 12 }}>
            {error}
          </p>
        )}
        {nextCursor && (
          <button
            type="button"
            className="btn-secondary"
            onClick={handleLoadMore}
            disabled={busy}
            style={{ marginTop: 14 }}
          >
            {busy ? "Loading…" : "Load more"}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="view">
      <div className="view-inner">
        <div className="view-head">
          <h2>Users</h2>
          <p>Registered users — who they are, when they joined, and signs of life.</p>
        </div>
        {content}
      </div>
    </div>
  );
}
