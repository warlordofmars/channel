// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import ChannelMark from "../components/ChannelMark.jsx";
import ChatHeader from "./ChatHeader.jsx";
import Composer from "./Composer.jsx";
import Icon from "../components/Icon.jsx";
import ToolResultBlock from "./ToolResultBlock.jsx";
import { submitFeedback } from "../api.js";
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

const COPIED_FEEDBACK_MS = 1500;

/**
 * Copy-to-clipboard helper. Returns a Promise that resolves on success.
 * Uses `navigator.clipboard.writeText` when available (secure-context
 * default); falls back to the `document.execCommand("copy")` legacy
 * path so the button still works in non-secure jsdom and older
 * environments. Errors are swallowed — clipboard failures should not
 * surface a console error to the user.
 */
async function copyTextToClipboard(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the legacy path.
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "absolute";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  try {
    ta.select();
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    document.body.removeChild(ta);
  }
}

/**
 * Copy-icon button rendered on the message-actions row of every
 * settled assistant turn. Owns its own `copied` state so the
 * "Copied" affordance is per-row — clicking one row's Copy button
 * doesn't tick the icon on every other row.
 */
function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef(null);

  useEffect(function clearTimerOnUnmount() {
    return function cleanup() {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  async function handleClick() {
    const ok = await copyTextToClipboard(text);
    if (!ok) return;
    setCopied(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(function clearCopiedFlag() {
      setCopied(false);
    }, COPIED_FEEDBACK_MS);
  }

  return (
    <button
      type="button"
      className="icon-btn"
      title={copied ? "Copied" : "Copy"}
      aria-label={copied ? "Copied" : "Copy"}
      onClick={handleClick}
    >
      <Icon name={copied ? "check" : "copy"} size={16} />
    </button>
  );
}

/**
 * Thumbs-up / thumbs-down pair rendered on the message-actions row of
 * every settled assistant turn (issue #146).
 *
 * State machine per row:
 *   - `null`             — no feedback recorded yet (default).
 *   - `"up"` / `"down"`  — that thumb is "filled". Clicking the OTHER
 *                          thumb swaps the active state. Re-clicking
 *                          the ACTIVE thumb is a no-op until the
 *                          backend grows a server-side clear operation
 *                          (e.g. DELETE) — otherwise the optimistic
 *                          clear would diverge from the stored signal
 *                          and skew evaluation data on reload.
 *   - `pending` boolean  — disables both buttons during an in-flight
 *                          submitFeedback call so a double-click can't
 *                          race the optimistic update.
 *
 * Optimistic: the visual state updates immediately on click; on a
 * network error we revert and surface nothing to the user (the SSE
 * stream / chat is unaffected — feedback is best-effort).
 */
function FeedbackButtons({ chatId, msgId, initialKind }) {
  const [kind, setKind] = useState(initialKind ?? null);
  const [pending, setPending] = useState(false);

  async function submitOrRevert(nextKind, prevKind) {
    setKind(nextKind);
    setPending(true);
    try {
      await submitFeedback(chatId, msgId, { kind: nextKind, note: null });
    } catch {
      setKind(prevKind);
    } finally {
      setPending(false);
    }
  }

  function handleUp() {
    // Re-clicking the active thumb is a no-op (see component docstring).
    if (kind === "up") return;
    submitOrRevert("up", kind);
  }
  function handleDown() {
    if (kind === "down") return;
    submitOrRevert("down", kind);
  }

  const upActive = kind === "up";
  const downActive = kind === "down";
  return (
    <>
      <button
        type="button"
        className={upActive ? "icon-btn is-active" : "icon-btn"}
        title="Good"
        aria-label="Good"
        aria-pressed={upActive}
        onClick={handleUp}
        disabled={pending}
      >
        <Icon name="thumb-up" size={16} />
      </button>
      <button
        type="button"
        className={downActive ? "icon-btn is-active" : "icon-btn"}
        title="Bad"
        aria-label="Bad"
        aria-pressed={downActive}
        onClick={handleDown}
        disabled={pending}
      >
        <Icon name="thumb-down" size={16} />
      </button>
    </>
  );
}

/**
 * Renders a single tool step row inside the expanded step list. The
 * three branches are:
 *
 *   1. `chain_cap` error — distinct copy ("I reached the tool-use
 *      limit"), per the strategy spec's "like having hands" UX brief:
 *      partial progress is acknowledged, not surfaced as a generic
 *      failure.
 *   2. Other error — `<toolName> failed (<errorType>)`.
 *   3. Finished / running — the tool name + status, plus the result
 *      summary (when present) rendered via `ToolResultBlock`. Future
 *      issues (#182, #183) extend `ToolResultBlock` to discriminate
 *      on `kind`; today only the default text-summary branch fires.
 */
function ToolStepRow({ step }) {
  if (step.status === "error" && step.errorType === "chain_cap") {
    const n = step.partialResultCount;
    return (
      <li className="tool-step-row tool-step-row-chain-cap">
        I reached the tool-use limit for this turn ({n} step{n === 1 ? "" : "s"}
        {" "}completed).
      </li>
    );
  }
  if (step.status === "error") {
    return (
      <li className="tool-step-row tool-step-row-error">
        {step.toolName} failed ({step.errorType})
      </li>
    );
  }
  return (
    <li className="tool-step-row">
      <div className="tool-step-row-head">
        <strong>{step.toolName}</strong>
        <span className="tool-step-status">{step.status}</span>
      </div>
      {step.summary && <ToolResultBlock kind={step.kind} summary={step.summary} />}
    </li>
  );
}

/**
 * Collapsible tool-step list rendered under an assistant message that
 * used tools. Default collapsed so the chat reads as conversation, not
 * a developer trace. The compact (collapsed) view still surfaces the
 * tool name + status so the user can see "the hands moved" without
 * expanding. Click the toggle to reveal the full step list with
 * `ToolResultBlock` per finished step.
 *
 * `expanded` + `onToggle` are owned by the parent (`Conversation`) so
 * the state survives the assistant turn's `msg_id` swap from the temp
 * client id to the persisted server id on the `done` SSE event. If
 * state lived here, that swap would remount this component and
 * collapse the list right as the stream finalises.
 */
function ToolStepList({ steps, expanded, onToggle }) {
  const noun = steps.length === 1 ? "step" : "steps";
  return (
    <div className="tool-steps">
      <button
        type="button"
        className="tool-steps-toggle"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <Icon name={expanded ? "chevron-down" : "chevron-right"} size={14} />
        {steps.length} tool {noun}
      </button>
      {expanded ? (
        <ol className="tool-steps-list">
          {steps.map((s) => (
            <ToolStepRow key={s.toolUseId} step={s} />
          ))}
        </ol>
      ) : (
        <ul className="tool-steps-compact">
          {steps.map((s) => (
            <li key={s.toolUseId}>
              {s.toolName} — {s.status}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
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

  // Tool-step expand/collapse state lives here (not in ToolStepList) so
  // it survives the streaming-assistant-turn `msg_id` swap from the
  // temp client id to the persisted server id on the `done` SSE event.
  // Keyed by the first step's `toolUseId` — backend-supplied via
  // `tool_started` and stable for the lifetime of the assistant turn.
  // Conversation itself doesn't remount on the swap; only the per-turn
  // row subtree does, so this Map persists across that remount.
  const [expandedSteps, setExpandedSteps] = useState(() => new Map());
  function toggleStepsExpanded(key) {
    setExpandedSteps(function flipKey(prev) {
      const next = new Map(prev);
      next.set(key, !(prev.get(key) ?? false));
      return next;
    });
  }

  // Reset the expanded-step Map when the URL :id changes (sidebar
  // navigation between chats). Conversation stays mounted across that
  // change — only the :id param flips — so without an explicit reset
  // the Map would retain keys from every prior chat and grow without
  // bound. Skip the no-op set when the Map is already empty so the
  // initial mount doesn't churn React state.
  useEffect(function resetExpandedStepsOnChatChange() {
    setExpandedSteps((prev) => (prev.size > 0 ? new Map() : prev));
  }, [chatId]);

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
                {t.toolSteps && t.toolSteps.length > 0 && (
                  <ToolStepList
                    steps={t.toolSteps}
                    expanded={expandedSteps.get(t.toolSteps[0].toolUseId) ?? false}
                    onToggle={() => toggleStepsExpanded(t.toolSteps[0].toolUseId)}
                  />
                )}
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
                    <CopyButton text={t.text} />
                    <button
                      type="button"
                      className="icon-btn"
                      title="Retry"
                      onClick={onRetry}
                      disabled={!retryEnabled}
                    >
                      <Icon name="refresh" size={16} />
                    </button>
                    <FeedbackButtons
                      chatId={chatId}
                      msgId={t.msg_id}
                      initialKind={t.feedback?.kind ?? null}
                    />
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
