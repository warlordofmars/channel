// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { parseToken, TOKEN_KEY } from "../lib/auth.js";
import Icon from "../components/Icon.jsx";
import AccountPopover from "./AccountPopover.jsx";
import ChatRowMenu from "./ChatRowMenu.jsx";
import RenameChatModal from "./RenameChatModal.jsx";
import DeleteChatModal from "./DeleteChatModal.jsx";
import { useChats } from "../hooks/ChatsContext.jsx";

/**
 * Bucket a chat into a recents group based on `last_message_at`.
 *
 * `now` is injected for deterministic tests; production callers pass
 * `Date.now()`.
 */
export function groupNameFor(lastMessageAt, now) {
  const ts = new Date(lastMessageAt).getTime();
  const ageMs = now - ts;
  const ONE_DAY = 24 * 60 * 60 * 1000;
  if (ageMs < ONE_DAY) return "Today";
  if (ageMs < 2 * ONE_DAY) return "Yesterday";
  if (ageMs < 7 * ONE_DAY) return "Previous 7 days";
  return "Older";
}

const GROUP_ORDER = ["Today", "Yesterday", "Previous 7 days", "Older"];

/**
 * 264px left nav. Primary items (New chat / Projects / Artifacts / Customize)
 * + a Search input that filters chats + time-grouped Recents + an account
 * row at the bottom.
 *
 * Translated from design-sources/app/shell.jsx `Sidebar` function. Per
 * spec deferrals: desktop traffic-lights branch dropped (web only),
 * "Relaunch to update" pill dropped (Electron only), "Channel Max" plan
 * label dropped (billing deferred). User name + email derive from the
 * mgmt JWT.
 *
 * Sign out clears the JWT and sends the user back to the marketing site at /.
 *
 * The `chats` prop holds API-shaped chats `{chat_id, title,
 * last_message_at, archived, ...}`. The server now filters archived
 * chats out of the default `list_chats` response (see issue #143), so
 * the Recents list naturally hides them. `useChatList` still owns the
 * full set the API returned; archive-management surfaces that need
 * archived rows opt in via `?include_archived=1`.
 * `onNewChat` fires when the user clicks the "New chat" primary action —
 * Shell wires this to `createChat()` + `navigate(/app/c/{id})`.
 */
export default function Sidebar({
  collapsed = false,
  onToggle = () => {},
  chats = [],
  onNewChat = () => {},
}) {
  const navigate = useNavigate();
  const { archiveChat } = useChats();
  const [searching, setSearching] = useState(false);
  const [search, setSearch] = useState("");
  const [acctOpen, setAcctOpen] = useState(false);
  const [pendingUpdateVersion, setPendingUpdateVersion] = useState(null);
  const [menuFor, setMenuFor] = useState(null);    // { chatId, chatTitle, anchorRect } | null
  const [renameFor, setRenameFor] = useState(null); // { chatId, currentTitle } | null
  const [deleteFor, setDeleteFor] = useState(null); // { chatId, chatTitle } | null
  // The chat route is defined in App.jsx as `/app/c/:id` — the param
  // name is `id`, not `chatId`. Destructure-rename for consistency
  // with Conversation.jsx.
  const { id: activeChatId = null } = useParams();

  function handleArchive(chatId) {
    // Fire-and-forget — the optimistic local removal happens inside
    // useChatList.archiveChat. If the active chat was just archived,
    // bounce to /app so the now-hidden row's URL doesn't 404 the user.
    // The .catch() swallows network errors to avoid noisy unhandled
    // promise rejections; the optimistic UI already removed the row,
    // and a backend failure surfaces on the next page load when the
    // archive flag fails to come back. A future error toast hook can
    // replace the swallow.
    archiveChat(chatId).catch(() => {});
    if (activeChatId === chatId) navigate("/app");
  }

  useEffect(() => {
    const desktop = window.channelDesktop;
    if (!desktop?.onUpdateStatus) return;
    desktop.onUpdateStatus(handleUpdateStatus);
    // No cleanup — desktop ipcRenderer.on listeners persist for the app lifetime
    // and the Sidebar mounts once per signed-in session.
  }, []);

  function handleUpdateStatus(payload) {
    if (payload?.state === "downloaded" && payload.version) {
      setPendingUpdateVersion(payload.version);
    }
  }

  function handleRelaunch() {
    window.channelDesktop?.relaunchToUpdate?.();
  }

  const token = localStorage.getItem(TOKEN_KEY) ?? "";
  const claims = parseToken(token) ?? {};
  const email = claims.email ?? "you@example.com";
  // Prefer Google's full display_name; fall back to the email's
  // local-part if absent (older tokens, test stubs, etc.).
  const userName =
    (claims.display_name && claims.display_name.trim()) ||
    email.split("@")[0] ||
    "You";
  // "John Carter" → "JC"; single-word name → first 2 letters.
  const initials = (() => {
    const words = userName.trim().split(/\s+/).filter(Boolean);
    if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
    return userName.slice(0, 2).toUpperCase();
  })();

  const groups = useMemo(() => {
    const now = Date.now();
    // Archived chats are filtered server-side by default (issue #143);
    // the Sidebar receives only non-archived rows for the Recents list.
    const filtered = chats.filter((c) =>
      (c.title ?? "").toLowerCase().includes(search.toLowerCase())
    );
    const byName = new Map();
    filtered.forEach((c) => {
      const name = groupNameFor(c.last_message_at, now);
      if (!byName.has(name)) byName.set(name, []);
      byName.get(name).push(c);
    });
    return GROUP_ORDER
      .filter((name) => byName.has(name))
      .map((name) => ({ name, items: byName.get(name) }));
  }, [chats, search]);
  const hasResults = groups.some((g) => g.items.length > 0);

  function signOut() {
    localStorage.removeItem(TOKEN_KEY);
    globalThis.location.assign("/");
  }

  return (
    <div className={"sb" + (collapsed ? " collapsed" : "")}>
      <div className="sb-top">
        <div className="sb-actions">
          <button type="button" className="icon-btn" title="Toggle sidebar" onClick={onToggle}><Icon name="sidebar" size={18} /></button>
          <button type="button" className="icon-btn" title="Search" onClick={() => setSearching((s) => !s)}>
            <Icon name="search" size={18} />
          </button>
        </div>
      </div>

      <div className="sb-scroll">
        {searching && (
          <div style={{ padding: "0 2px 8px" }}>
            <div className="composer" style={{ padding: "7px 10px", boxShadow: "none" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--ink-faint)" }}>
                <Icon name="search" size={16} />
                <input
                  autoFocus
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search chats"
                  style={{
                    border: "none",
                    outline: "none",
                    background: "transparent",
                    color: "var(--ink)",
                    fontFamily: "var(--font-sans)",
                    fontSize: 13.5,
                    width: "100%",
                  }}
                />
              </div>
            </div>
          </div>
        )}

        <button type="button" className="nav-item primary" onClick={onNewChat}>
          <span className="ic"><Icon name="plus" size={18} /></span> New chat
        </button>
        <button type="button" className="nav-item" onClick={() => navigate("/app/projects")}>
          <span className="ic"><Icon name="projects" size={18} /></span> Projects
        </button>
        <button type="button" className="nav-item" onClick={() => navigate("/app/artifacts")}>
          <span className="ic"><Icon name="artifacts" size={18} /></span> Artifacts
        </button>
        <button type="button" className="nav-item" onClick={() => navigate("/app/customize")}>
          <span className="ic"><Icon name="customize" size={18} /></span> Customize
        </button>

        {groups.map((g) => (
          <div key={g.name}>
            <div className="sb-section">{g.name}</div>
            {g.items.map((c) => (
              <div key={c.chat_id} className="recent-wrap">
                <button
                  type="button"
                  className={"recent" + (c.chat_id === activeChatId ? " active" : "")}
                  onClick={() => navigate(`/app/c/${c.chat_id}`)}
                >
                  {c.title}
                </button>
                <button
                  type="button"
                  className="recent-menu-btn"
                  aria-label={`More options for ${c.title}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    const rect = e.currentTarget
                      .closest(".recent-wrap")
                      .getBoundingClientRect();
                    setMenuFor({ chatId: c.chat_id, chatTitle: c.title, anchorRect: rect });
                  }}
                >
                  <Icon name="more-vertical" size={16} />
                </button>
              </div>
            ))}
          </div>
        ))}
        {!hasResults && search && (
          <div style={{ padding: "20px 10px", fontSize: 13, color: "var(--ink-faint)" }}>
            No chats match &ldquo;{search}&rdquo;.
          </div>
        )}
      </div>

      <div className="sb-foot">
        {pendingUpdateVersion && (
          <button
            type="button"
            className="update-pill"
            onClick={handleRelaunch}
          >
            Relaunch to update v{pendingUpdateVersion}
          </button>
        )}
        <div className="account-wrap">
          {acctOpen && (
            <>
              <div className="backdrop" onClick={() => setAcctOpen(false)} />
              <AccountPopover userName={userName} email={email} onSignOut={signOut} />
            </>
          )}
          <button type="button" className="account" onClick={() => setAcctOpen((o) => !o)}>
            <span className="avatar">{initials}</span>
            <div style={{ flex: 1, textAlign: "left" }}>
              <div className="nm">{userName}</div>
              <div className="pl" style={{ fontSize: 11.5, color: "var(--ink-faint)" }}>{email}</div>
            </div>
            <Icon name="chevron-down" size={15} style={{ color: "var(--ink-faint)" }} />
          </button>
        </div>
      </div>

      {menuFor && (
        <ChatRowMenu
          open
          anchorRect={menuFor.anchorRect}
          onClose={() => setMenuFor(null)}
          onRename={() => {
            setRenameFor({ chatId: menuFor.chatId, currentTitle: menuFor.chatTitle });
          }}
          onArchive={() => {
            handleArchive(menuFor.chatId);
          }}
          onDelete={() => {
            setDeleteFor({ chatId: menuFor.chatId, chatTitle: menuFor.chatTitle });
          }}
        />
      )}
      {renameFor && (
        <RenameChatModal
          open
          chatId={renameFor.chatId}
          currentTitle={renameFor.currentTitle}
          onClose={() => setRenameFor(null)}
        />
      )}
      {deleteFor && (
        <DeleteChatModal
          open
          chatId={deleteFor.chatId}
          chatTitle={deleteFor.chatTitle}
          isActive={activeChatId === deleteFor.chatId}
          onClose={() => setDeleteFor(null)}
        />
      )}
    </div>
  );
}
