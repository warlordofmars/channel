// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Icon from "../../components/Icon.jsx";
import { ApiError, getAdminUser } from "../../api.js";
import { fmtDate } from "./Users.jsx";

const MUTED_STYLE = { fontSize: 13.5, color: "var(--ink-soft)" };
const SECTION_HEADING_STYLE = {
  fontSize: 15,
  fontWeight: 600,
  margin: "26px 0 10px",
};
const BACK_LINK_STYLE = {
  color: "var(--ink-soft)",
  textDecoration: "none",
  fontSize: 13,
};

/**
 * Render an ISO-8601 timestamp as `YYYY-MM-DD HH:MM:SS` (or an em dash
 * for null) — audit events want the time of day, not just the date.
 */
function fmtDateTime(iso) {
  return iso ? iso.slice(0, 19).replace("T", " ") : "—";
}

/**
 * `/app/admin/users/:id` — single-user admin view (#238).
 *
 * Header repeats the list-row fields, then "Recent chats" (≤10, chat
 * titles link to the existing `/app/c/:id` conversation view) and
 * "Recent audit events" (≤25 over the last 7 days, timeline-style).
 * An unknown user 404s server-side (#235) and renders a dedicated
 * not-found state rather than a generic error.
 */
export default function UserDetail() {
  const { id } = useParams();
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null); // null | "notfound" | "load"

  useEffect(
    function loadUser() {
      let cancelled = false;
      setDetail(null);
      setError(null);
      getAdminUser(id)
        .then(function onDetail(data) {
          if (cancelled) return;
          setDetail(data);
        })
        .catch(function onDetailError(err) {
          if (cancelled) return;
          setError(
            err instanceof ApiError && err.status === 404 ? "notfound" : "load",
          );
        });
      return function cancelLoadUser() {
        cancelled = true;
      };
    },
    [id],
  );

  let content;
  if (error === "notfound") {
    content = (
      <div className="view-head">
        <h2>User not found</h2>
        <p>
          No registered user matches{" "}
          <span style={{ fontFamily: "var(--font-mono)" }}>{id}</span>.{" "}
          <Link to="/app/admin/users" style={{ color: "var(--accent-ink)" }}>
            Back to users
          </Link>
        </p>
      </div>
    );
  } else if (error === "load") {
    content = (
      <div className="view-head">
        <h2>Something went wrong</h2>
        <p>
          Could not load this user.{" "}
          <Link to="/app/admin/users" style={{ color: "var(--accent-ink)" }}>
            Back to users
          </Link>
        </p>
      </div>
    );
  } else if (detail === null) {
    content = <p style={MUTED_STYLE}>Loading user…</p>;
  } else {
    const { user, recent_chats: recentChats, recent_audit_events: auditEvents } =
      detail;
    content = (
      <div>
        <div className="view-head">
          <p style={{ marginBottom: 8 }}>
            <Link to="/app/admin/users" style={BACK_LINK_STYLE}>
              Back to users
            </Link>
          </p>
          <h2>{user.email}</h2>
          <p>
            Joined {fmtDate(user.created_at)} · Last chat{" "}
            {fmtDate(user.last_chat_at)} · {user.chat_count}{" "}
            {user.chat_count === 1 ? "chat" : "chats"} · Last login{" "}
            {fmtDate(user.last_login_at)}
          </p>
        </div>

        <section>
          <h3 style={SECTION_HEADING_STYLE}>Recent chats</h3>
          {recentChats.length === 0 ? (
            <p style={MUTED_STYLE}>No chats yet.</p>
          ) : (
            <table className="art-table" data-testid="admin-user-chats">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Created</th>
                  <th>Last message</th>
                  <th>Messages</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {recentChats.map((c) => (
                  <tr key={c.chat_id}>
                    <td>
                      <Link
                        to={`/app/c/${c.chat_id}`}
                        style={{
                          color: "var(--accent-ink)",
                          textDecoration: "none",
                          fontWeight: 600,
                        }}
                      >
                        {c.title || "Untitled"}
                      </Link>
                    </td>
                    <td className="mono">{fmtDate(c.created_at)}</td>
                    <td className="mono">{fmtDate(c.last_message_at)}</td>
                    <td className="mono">{c.message_count}</td>
                    <td className="mono">{c.archived ? "archived" : "active"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section>
          <h3 style={SECTION_HEADING_STYLE}>Recent audit events</h3>
          {auditEvents.length === 0 ? (
            <p style={MUTED_STYLE}>No audit events in the last 7 days.</p>
          ) : (
            <div data-testid="admin-user-audit">
              {auditEvents.map((e) => (
                <div
                  className="list-row"
                  key={e.event_id}
                  style={{ cursor: "default" }}
                >
                  <span className="badge">
                    <Icon name="shield" size={18} />
                  </span>
                  <div className="info">
                    <div className="ti">{e.event_type}</div>
                    <div className="sub">
                      {fmtDateTime(e.created_at)}
                      {e.details ? " · " + JSON.stringify(e.details) : ""}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    );
  }

  return (
    <div className="view">
      <div className="view-inner">{content}</div>
    </div>
  );
}
