// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  endSession,
  listSessions,
  revokeAllSessions,
  revokeSession,
} from "../../api.js";
import Icon from "../../components/Icon.jsx";
import Modal from "../../components/Modal.jsx";
import { relativeTime } from "./artifactHelpers.js";

/**
 * A short, stable handle for one signed-in device.
 *
 * The API deliberately returns no human device name. `device_id` is an opaque
 * server-generated value, and the refresh row's `display_name` is the *user's*
 * own name carried across rotations (#292) — identical on every one of their
 * rows, so it would label nothing. A real label ("MacBook Pro", "Chrome on
 * Windows") is a User-Agent-derived attribute that belongs with the mint path
 * and does not exist yet.
 *
 * So this shortens the opaque id instead of inventing a device name. It is
 * enough to tell two rows apart and to match a row against the one you are
 * about to end, which is what the list is for. Non-alphanumerics are dropped
 * so the handle can never be mistaken for structure.
 */
export function deviceLabel(deviceId) {
  const short = String(deviceId ?? "").replace(/[^a-zA-Z0-9]/g, "").slice(0, 8);
  return short ? `Device ${short}` : "Unnamed device";
}

/**
 * The meta line under a device's label.
 *
 * `last_used_at` is the one the user reasons about ("is this still me?"), so
 * it leads; `issued_at` follows as the sign-in date. `relativeTime` answers
 * the empty string for a missing or unparseable timestamp, and a row missing
 * the field it is keyed on renders without that clause rather than with a
 * bogus "56y ago".
 */
function sessionMeta(session) {
  const used = relativeTime(session.last_used_at);
  const issued = relativeTime(session.issued_at);
  const parts = [];
  if (used) parts.push(`Last used ${used}`);
  if (issued) parts.push(`signed in ${issued}`);
  if (parts.length === 0) return "No activity recorded";
  return parts.join(" · ");
}

/**
 * "Signed-in devices" — the user-facing half of the sessions API (#296,
 * #293, epic #241).
 *
 * One row per device, newest first, straight from `GET /api/me/sessions`;
 * the server has already collapsed the refresh-row chain each device burns
 * through under hard rotation (#290), so no de-duplication happens here.
 *
 * Three properties of the backing API shape this view, and each is a thing
 * the UI must NOT paper over:
 *
 * 1. **The list is eventually consistent.** It walks `RefreshByUserIndex`,
 *    and DynamoDB refuses `ConsistentRead` on a GSI. A device that signed in
 *    moments ago can be missing, and — the case that actually reaches the
 *    user — revoking a row this list just showed can answer 404 while the
 *    index catches up. A 404 here means the session is gone, which is what
 *    the user asked for, so it is reported as success and the list is
 *    refreshed rather than surfaced as an error.
 * 2. **Neither DELETE returns a count.** 204, no body, deliberately: the
 *    index cannot promise a post-condition, so "3 sessions revoked" would be
 *    a claim the storage layer does not support. This view never displays
 *    one; it re-reads the list instead and shows what is actually there.
 * 3. **"Sign out everywhere" includes this device.** There is no
 *    all-but-current endpoint — the mgmt JWT carries no `device_id` claim, so
 *    the server cannot identify the caller's own row to spare it. It
 *    denylists the caller's access token in the same call instead, so on
 *    success this session is already dead and any subsequent request would
 *    401. The view therefore ends the session locally and routes to the login
 *    page rather than returning to a view that can no longer load anything.
 *
 * The same missing `device_id` claim is why no row is badged "this device":
 * the client genuinely cannot tell which one it is, and guessing from
 * `last_used_at` would be a confident lie about a security surface.
 */
export default function Sessions() {
  const [sessions, setSessions] = useState([]);
  const [status, setStatus] = useState("loading"); // loading | ready | error
  // `null` when no confirm is open, else { scope: "device" | "all", session }.
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");

  // A revoke resolves outside any effect, so its `setState` calls need a
  // mounted guard of their own; the load effect's `cancelled` closure covers
  // only the load. Both exist for the same reason — this view is reachable
  // from the sidebar and the user can leave mid-request.
  const mountedRef = useRef(true);
  useEffect(function trackMounted() {
    mountedRef.current = true;
    return function markUnmounted() {
      mountedRef.current = false;
    };
  }, []);

  const load = useCallback(function load() {
    return listSessions()
      .then(function onSessions(rows) {
        if (!mountedRef.current) return;
        setSessions(rows);
        setStatus("ready");
      })
      .catch(function onSessionsError() {
        if (!mountedRef.current) return;
        setStatus("error");
      });
  }, []);

  useEffect(
    function loadOnMount() {
      load();
    },
    [load],
  );

  function askRevokeDevice(session) {
    setNotice("");
    setActionError("");
    setConfirm({ scope: "device", session });
  }

  function askRevokeAll() {
    setNotice("");
    setActionError("");
    setConfirm({ scope: "all" });
  }

  function closeConfirm() {
    // Ignored while a revoke is in flight: the request cannot be recalled, and
    // dropping the dialog mid-flight would leave the user without the result.
    if (busy) return;
    setConfirm(null);
  }

  async function revokeOneDevice(session) {
    try {
      await revokeSession(session.device_id);
      setNotice(`${deviceLabel(session.device_id)} signed out.`);
    } catch (error) {
      if (error?.status !== 404) throw error;
      // Already gone — the index had not caught up when the list was read.
      // The user's goal is satisfied, so this is a success with a footnote,
      // not a failure. Falling through re-reads the list either way.
      setNotice(`${deviceLabel(session.device_id)} was already signed out.`);
    }
    await load();
  }

  async function revokeEveryDevice() {
    await revokeAllSessions();
    // This session's access token was just denylisted server-side, so there is
    // nothing left to render an updated list with. Clear local state and route
    // to the login page — `endSession` owns both halves (#483).
    endSession();
  }

  async function confirmRevoke() {
    const pending = confirm;
    setBusy(true);
    setActionError("");
    try {
      if (pending.scope === "all") {
        await revokeEveryDevice();
        // No success notice and no list re-read: this session is already dead
        // server-side and the view is on its way to the login page. The
        // `finally` below still settles `busy`/`confirm`, which is harmless —
        // it is guarded on the component still being mounted.
        return;
      }
      await revokeOneDevice(pending.session);
    } catch {
      if (mountedRef.current) {
        setActionError("Couldn't sign that out. Check your connection and try again.");
      }
    } finally {
      if (mountedRef.current) {
        setBusy(false);
        setConfirm(null);
      }
    }
  }

  const confirmingAll = confirm?.scope === "all";

  return (
    <div className="view">
      <div className="view-inner" style={{ maxWidth: 720 }}>
        <div className="view-head">
          <h2>Signed-in devices</h2>
          <p>
            Every device currently signed in to your account. Sign one out to end
            its session immediately.
          </p>
        </div>

        {status === "loading" && <p className="mem-note">Loading…</p>}

        {status === "error" && (
          <p className="mem-note" role="alert">
            Couldn&apos;t load your signed-in devices. Reload to try again.
          </p>
        )}

        {status === "ready" && (
          <>
            {/*
              `.mem-note` carries no bottom margin (it is written to sit
              under a list, not above one), so these two get one inline —
              without it the message collides with the "Devices" heading
              directly below.
            */}
            {notice && (
              <p className="mem-note" role="status" style={{ marginBottom: 14 }}>
                {notice}
              </p>
            )}

            {actionError && (
              <p className="mem-note" role="alert" style={{ marginBottom: 14 }}>
                {actionError}
              </p>
            )}

            {sessions.length === 0 && (
              <p className="mem-empty">
                No other signed-in devices are being tracked. Sessions started
                before device tracking was added — and sign-ins that use a
                development shortcut — don&apos;t appear here; they end on their
                own within the hour.
              </p>
            )}

            {sessions.length > 0 && (
              <div className="set-group">
                <h3>Devices</h3>
                {sessions.map((session) => (
                  <SessionRow
                    key={session.device_id}
                    session={session}
                    onRevoke={askRevokeDevice}
                  />
                ))}
              </div>
            )}

            <div className="set-group">
              <h3>Everywhere</h3>
              <div className="set-row">
                <div>
                  <div className="lbl">Sign out of every device</div>
                  <div className="hint">
                    Ends every session on the list, including this one — so
                    you&apos;ll be signed out here and sent back to the login
                    page. Use this if you think someone else has your account.
                  </div>
                </div>
                <div className="ctl">
                  {/*
                    `nowrap` because the long hint beside it squeezes the
                    control column enough to break the label across two
                    lines — verified in the running app, not guessed at.
                  */}
                  <button
                    type="button"
                    className="btn-danger"
                    onClick={askRevokeAll}
                    style={{ whiteSpace: "nowrap" }}
                  >
                    Sign out everywhere
                  </button>
                </div>
              </div>
            </div>
          </>
        )}

        <Modal open={confirm !== null} onClose={closeConfirm}>
          <h2 className="modal-heading">
            {confirmingAll ? "Sign out everywhere?" : "Sign out this device?"}
          </h2>
          <p className="modal-body">
            {confirmingAll
              ? "Every device signed in to your account will be signed out, " +
                "including this one. You'll be returned to the login page."
              : `${deviceLabel(confirm?.session?.device_id)} will be signed out. ` +
                "It will need to sign in again to use your account."}
          </p>
          <div className="modal-buttons">
            <button
              type="button"
              className="btn-secondary"
              onClick={closeConfirm}
              disabled={busy}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn-danger"
              onClick={confirmRevoke}
              disabled={busy}
            >
              {busy ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </Modal>
      </div>
    </div>
  );
}

function SessionRow({ session, onRevoke }) {
  function revoke() {
    onRevoke(session);
  }
  return (
    <div className="set-row">
      <div>
        <div className="lbl">
          <Icon name="shield" size={14} /> {deviceLabel(session.device_id)}
        </div>
        <div className="hint">{sessionMeta(session)}</div>
      </div>
      <div className="ctl">
        <button
          type="button"
          className="btn-secondary"
          onClick={revoke}
          aria-label={`Sign out ${deviceLabel(session.device_id)}`}
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
