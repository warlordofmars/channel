// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import ChannelMark from "../components/ChannelMark.jsx";
import ChatHeader from "./ChatHeader.jsx";
import Composer from "./Composer.jsx";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
import { useChats } from "../hooks/ChatsContext.jsx";
import { useChatStream } from "../hooks/useChatStream.js";
import { renderMarkdown } from "./renderMarkdown.jsx";
import { cachedModels, loadModels, mergeWithDisplayMeta } from "./data.js";

// Strip Bedrock's id wrappers down to the short id the API serves.
// Bedrock returns model ids like `us.anthropic.claude-sonnet-4-6`
// (cross-region inference profile) or `anthropic.claude-sonnet-4-6`
// (base foundation-model). We also strip the trailing date-and-version
// suffix (e.g. `-20251001-v1:0` or `-v1`) before lookup.
function stripBedrockPrefix(raw) {
  return raw
    .replace(/^(us|global)\.anthropic\.|^anthropic\./, "")
    .replace(/(-\d{8})?-v\d+(:\d+)?$/, "");
}

function modelLabelFromList(raw, models) {
  if (!raw) return "";
  if (!models) return raw;
  const shortId = stripBedrockPrefix(raw);
  const display = models.find((m) => m.id === shortId);
  return display ? display.name : raw;
}

/**
 * Streaming conversation pane backed by the real SSE-driven
 * `useChatStream` hook. The URL `:id` is the canonical chat id; the hook
 * loads history on mount and exposes `{ turns, send, abort, status }`.
 *
 * First-message kick-off: when ChatHome navigates here it stashes the
 * first user message in `location.state.firstMessage` (shape:
 * `{ message, model, effort, attachments }`). On mount we forward that
 * directly to `useChatStream.send(...)`. `sentFirstRef` guards against
 * re-firing on subsequent renders (React state changes, StrictMode
 * double-effect, etc.).
 *
 * The follow-up Composer at the bottom of the pane wraps the hook's
 * `send` so that the Composer's `onSend(text, atts)` shape is
 * preserved.
 *
 * Message-actions row (copy/retry/thumbs) is rendered per assistant
 * turn once that turn is no longer streaming. Buttons are no-ops at
 * Phase 7a.
 */
export default function Conversation() {
  const { id: chatId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const chats = useChats();
  const currentChat = chats.chats.find((c) => c.chat_id === chatId) ?? null;
  const { turns, send, regenerate, status } = useChatStream(chatId, {
    // Phase 7d: backend auto-titles fresh chats after the first reply
    // and emits a ``title_suggested`` SSE event. Update the sidebar
    // immediately (server already persisted via storage.patch_chat).
    onTitleSuggested: (title) => chats.renameChatLocal(chatId, title),
  });
  const prefs = useChannelPrefs();
  const ref = useRef(null);
  const sentFirstRef = useRef(false);
  const composerRef = useRef(null);

  // Model allowlist comes from /api/models (issue #148 dropped the
  // hardcoded fallback). Until it lands we wrap the prefs.model id in
  // local display meta so the Composer can still send.
  const [models, setModels] = useState(() => cachedModels());

  useEffect(function loadModelAllowlist() {
    // `loadModels()` is internally cached, so a hot mount after another
    // consumer fetched is a no-op.
    loadModels()
      .then(setModels)
      .catch(function onModelsFetchError() { setModels([]); });
  }, []);

  // Auto-scroll to the bottom on any turns change (catches each stream tick).
  useEffect(function autoScrollOnTurns() {
    const el = ref.current;
    el.scrollTop = el.scrollHeight;
  }, [turns]);

  // First-message kick-off from route state. ChatHome stashes the user's
  // initial message in `location.state.firstMessage`; we forward it once
  // and IMMEDIATELY clear it from history so a back-navigation or remount
  // (e.g. clicking the same chat in the sidebar later) doesn't re-fire
  // the send and ask the agent to respond to its own historical first
  // user message.
  useEffect(function consumeFirstMessage() {
    if (sentFirstRef.current) return;
    const first = location.state?.firstMessage;
    if (chatId && first) {
      sentFirstRef.current = true;
      navigate(location.pathname, { replace: true, state: null });
      send(first);
    }
  }, [chatId, location.state, location.pathname, navigate, send]);

  const modelObj =
    (models && models.find((m) => m.id === prefs.model)) ||
    (models && models[0]) ||
    mergeWithDisplayMeta({ id: prefs.model });
  const setModelObj = (m) => prefs.setModel(m.id);
  const followUp = (text, atts) =>
    // Backend expects model as a short id string, not the picker's display object.
    send({ message: text, model: modelObj.id, effort: prefs.effort, attachments: atts });
  const onFollowUpClick = (suggestion) => {
    composerRef.current?.setText(suggestion);
    composerRef.current?.focus();
  };
  const noop = () => {};

  return (
    <>
      <ChatHeader chat={currentChat} />
      <div className="convo" ref={ref}>
        <div className="convo-inner">
          {status === "error" && (
            <div className="convo-error" role="alert">
              Something went wrong loading this conversation.
            </div>
          )}
          {turns.map((t, i) => {
            const isLast = i === turns.length - 1;
            // Retry icon is the canonical regenerate trigger. It's the
            // last assistant turn's responsibility; older turns get a
            // no-op so the row layout stays consistent.
            const retryEnabled =
              isLast && t.role === "assistant" && !t.streaming;
            const onRetry = retryEnabled ? () => regenerate({}) : noop;
            return t.role === "user" ? (
              <div className="turn user" key={t.msg_id}>
                {t.atts && t.atts.length > 0 && (
                  <div
                    className="attaches"
                    style={{ justifyContent: "flex-end", marginBottom: 0 }}
                  >
                    {t.atts.map((a, j) => (
                      <div className="chip" key={j}>
                        <span className="tile"><Icon name={a.ic} size={15} /></span>
                        <span>{a.name}</span>
                      </div>
                    ))}
                  </div>
                )}
                <div className="bubble">{t.text}</div>
              </div>
            ) : (
              <div
                className="turn"
                key={t.msg_id}
                {...(!t.streaming && { "data-testid": "assistant-turn-idle" })}
              >
                <div className="assistant-head">
                  <ChannelMark size={20} />
                  <span className="nm">Channel</span>
                  <span className="mdl">{modelLabelFromList(t.model, models)}</span>
                </div>
                <div className="msg">{renderMarkdown(t.text, t.streaming)}</div>
                {t.artifact && (
                  <div className="art-inline" onClick={noop}>
                    <div className="ah">
                      <span className="ic"><Icon name={t.artifact.ic} size={18} /></span>
                      <div>
                        <div className="at">{t.artifact.title}</div>
                        <div className="as">{t.artifact.kind}</div>
                      </div>
                      <span style={{ marginLeft: "auto", color: "var(--ink-faint)" }}>
                        <Icon name="expand" size={16} />
                      </span>
                    </div>
                  </div>
                )}
                {!t.streaming && (
                  <div className="msg-actions">
                    <button type="button" className="icon-btn" title="Copy" onClick={noop}>
                      <Icon name="copy" size={16} />
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      title="Retry"
                      onClick={onRetry}
                      disabled={!retryEnabled}
                    >
                      <Icon name="refresh" size={16} />
                    </button>
                    <button type="button" className="icon-btn" title="Good" onClick={noop}>
                      <Icon name="thumb-up" size={16} />
                    </button>
                    <button type="button" className="icon-btn" title="Bad" onClick={noop}>
                      <Icon name="thumb-down" size={16} />
                    </button>
                  </div>
                )}
                {isLast && t.followUps && t.followUps.length > 0 && (
                  <div className="followups">
                    {t.followUps.map((s) => (
                      <button
                        key={s}
                        type="button"
                        className="followup-chip"
                        onClick={() => onFollowUpClick(s)}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
      <div className="bottom-composer">
        <Composer
          ref={composerRef}
          model={modelObj}
          effort={prefs.effort}
          setModel={setModelObj}
          setEffort={prefs.setEffort}
          onSend={followUp}
          placeholder="Reply…"
        />
      </div>
    </>
  );
}
