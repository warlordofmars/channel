// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { endSession, listSessions, revokeAllSessions, revokeSession } from "../../api.js";

function formatDate(isoStr) {
  if (!isoStr) return "Unknown";
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return isoStr;
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
    });
  } catch (e) {
    return isoStr;
  }
}

export default function Sessions() {
  const [sessions, setSessions] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    listSessions()
      .then((data) => {
        if (active) {
          setSessions(data.sessions);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (active) {
          setError("Failed to load sessions.");
          setLoading(false);
          console.error(err);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function handleRevoke(deviceId) {
    try {
      await revokeSession(deviceId);
      setSessions((prev) => prev.filter((s) => s.device_id !== deviceId));
    } catch (err) {
      console.error(err);
      setError("Failed to revoke session.");
    }
  }

  async function handleRevokeAll() {
    if (!window.confirm("Sign out of all devices? This will also sign you out of your current session.")) return;
    try {
      await revokeAllSessions();
      endSession();
    } catch (err) {
      console.error(err);
      setError("Failed to sign out everywhere.");
    }
  }

  if (loading) {
    return (
      <div className="set-group">
        <h3>Active Sessions</h3>
        <div className="hint">Loading sessions…</div>
      </div>
    );
  }

  return (
    <div className="set-group">
      <h3>Active Sessions</h3>
      <p className="hint">
        Manage devices currently signed into your account.
      </p>
      {error && <div className="hint" role="alert" style={{ color: "var(--color-forget)" }}>{error}</div>}
      
      {sessions && sessions.length === 0 ? (
        <div className="hint">No active sessions found.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px", marginTop: "12px" }}>
          {sessions && sessions.map((s) => (
            <div key={s.device_id} className="set-row" style={{ alignItems: "center" }}>
              <div>
                <div className="lbl" title={s.device_id}>
                  {`Device • ${s.device_id.split("-")[0] || s.device_id}`}
                </div>
                <div className="hint">Last used: {formatDate(s.last_used_at)}</div>
              </div>
              <div className="ctl">
                <button
                  type="button"
                  className="ck"
                  onClick={() => handleRevoke(s.device_id)}
                >
                  Sign out
                </button>
              </div>
            </div>
          ))}
          <div className="set-row" style={{ borderTop: "1px solid var(--border)", paddingTop: "12px" }}>
            <div />
            <div className="ctl">
              <button
                type="button"
                className="btn-primary"
                onClick={handleRevokeAll}
                style={{ background: "var(--color-forget)" }}
              >
                Sign out of all devices
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
