// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import AssetCard from "./AssetCard.jsx";
import InlineImage, { isInlineImage } from "./InlineImage.jsx";
import InlineData, { isInlineData } from "./InlineData.jsx";
import InlineDocument, { isInlineDocument } from "./InlineDocument.jsx";
import ChannelMark from "../components/ChannelMark.jsx";
import ChatHeader from "./ChatHeader.jsx";
import Composer from "./Composer.jsx";
import Icon from "../components/Icon.jsx";
import ToolResultBlock from "./ToolResultBlock.jsx";
import {
  getChatMCPSettings,
  listMCPServers,
  putChatMCPSettings,
  submitFeedback,
} from "../api.js";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
import { useChats } from "../hooks/ChatsContext.jsx";
import { useChatStream } from "../hooks/useChatStream.js";
import { renderMarkdown, scanFences } from "./renderMarkdown.jsx";
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

// #327 → #362: a `kind=code` asset carrying a numeric `source.fence_index`
// originates from fence extraction and is a candidate for inline decoration.
function isFenceCodeAsset(a) {
  return (
    a.kind === "code" &&
    a.source != null &&
    typeof a.source.fence_index === "number"
  );
}

// The set of fence ordinals actually present in this turn's message text.
// Mirrors renderMarkdown's own scan so the partition and the decorate path
// agree on which fences are locatable.
function locatedFenceIndices(text) {
  return new Set(scanFences(text || "").map((f) => f.fenceIndex));
}

// A fence-code asset is DECORATED inline only when its ordinal is actually
// located in the message text — renderMarkdown then renders the native code
// block with an "open in panel" affordance in place (not a card). A located
// asset must therefore be excluded from the standalone-card partition so the
// fence isn't double-rendered. A fence-code asset whose ordinal is NOT
// located (scan drift / text mismatch) is NOT decorated, so it must still
// fall through to a standalone card — otherwise the asset and its panel
// affordances would silently disappear (#362 acceptance: non-located
// fence-origin code still cards).
function isDecoratedInline(a, located) {
  return isFenceCodeAsset(a) && located.has(a.source.fence_index);
}

// Map<fence_index, asset> for the fenced blocks this turn decorates inline —
// keyed off fences actually located in `text`. Runs per render, so it skips
// the O(text) fence scan entirely unless the turn actually carries a
// fence-code candidate (the common turn has none).
export function codeAssetsByFence(assets, text) {
  const list = assets || [];
  if (!list.some(isFenceCodeAsset)) return new Map();
  const located = locatedFenceIndices(text);
  const map = new Map();
  for (const a of list) {
    if (isDecoratedInline(a, located)) map.set(a.source.fence_index, a);
  }
  return map;
}

// The assets rendered as standalone cards under the message. Only fences
// decorated inline (located) are excluded; uploads, code-exec images, code
// assets without a fence_index, AND non-located fence-code assets (scan
// drift) all still card. Called per render for BOTH turn branches, so the
// no-fence-code common case short-circuits to O(#assets) without scanning
// the message text.
export function standaloneAssets(assets, text) {
  const list = assets || [];
  if (!list.some(isFenceCodeAsset)) return list;
  const located = locatedFenceIndices(text);
  return list.filter((a) => !isDecoratedInline(a, located));
}

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
      {(step.summary || step.payload) && (
        <ToolResultBlock kind={step.kind} summary={step.summary} payload={step.payload} />
      )}
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
 *
 * #212: a turn carrying `streamError` (SSE `error` frame or unexpected
 * EOF — see useChatStream) renders an error chip with the user-safe
 * message instead of the actions row; on the last turn the chip also
 * offers Retry, which reuses the regenerate path (the user turn is
 * already persisted server-side).
 */
export default function Conversation() {
  const { id: chatId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const chats = useChats();
  const currentChat = chats.chats.find((c) => c.chat_id === chatId) ?? null;
  const { turns, send, regenerate, status, loadOlder, hasOlder } = useChatStream(chatId, {
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

  // #207 — MCP servers list (per-account) + per-chat override settings.
  // Both are tolerant of fetch failures: the picker simply hides if
  // mcpServers stays null. Per-chat settings default to inherit mode
  // on any error so the picker still works without persistence.
  const [mcpServers, setMcpServers] = useState(null);
  const [mcpSettings, setMcpSettings] = useState(null);

  useEffect(() => {
    let cancelled = false;
    listMCPServers()
      .then(({ servers }) => {
        if (!cancelled) setMcpServers(servers);
      })
      .catch(() => {
        /* tolerate — the pill simply doesn't render */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!chatId) return undefined;
    let cancelled = false;
    getChatMCPSettings(chatId)
      .then((s) => {
        if (!cancelled) setMcpSettings(s);
      })
      .catch(() => {
        /* v8 ignore next 3 -- race-condition cleanup; defensive */
        if (!cancelled) {
          setMcpSettings({ mode: "inherit", explicit_server_ids: [] });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [chatId]);

  async function handleMcpChange(next) {
    setMcpSettings(next);
    // Mirror the fetch effect's guard — without a chatId we'd POST to
    // /api/chats/undefined/mcp on edge cases (early renders, unexpected
    // routing). State stays local until a real chatId is in scope.
    if (!chatId) return;
    try {
      await putChatMCPSettings(chatId, next);
    } catch (e) {
      console.error("putChatMCPSettings", e);
    }
  }

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

  // #270: when "Load earlier messages" prepends an older page,
  // `handleLoadEarlier` records the scroll height just before the turns
  // grow at the TOP. The auto-scroll effect reads it and keeps the
  // reader's position (scrollTop += height delta) instead of snapping to
  // the bottom, which would defeat backward pagination.
  const prependAnchorRef = useRef(null);
  const prevFirstIdRef = useRef(null);

  // Auto-scroll to the bottom on any turns change (catches each stream
  // tick) — EXCEPT right after an older page is prepended, where we
  // preserve the reader's position. A prepend is the only case where the
  // head turn changes while an anchor is set; a no-op/failed Load-earlier
  // click leaves the head unchanged, so we still scroll to the bottom and
  // always clear the anchor so a stale one can't hijack a later tail
  // change.
  useEffect(function autoScrollOnTurns() {
    const el = ref.current;
    const firstId = turns.length ? turns[0].msg_id : null;
    const prepended =
      prependAnchorRef.current != null && firstId !== prevFirstIdRef.current;
    prevFirstIdRef.current = firstId;
    if (prepended) {
      el.scrollTop += el.scrollHeight - prependAnchorRef.current;
    } else {
      el.scrollTop = el.scrollHeight;
    }
    prependAnchorRef.current = null;
  }, [turns]);

  function handleLoadEarlier() {
    prependAnchorRef.current = ref.current.scrollHeight;
    loadOlder();
  }

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
  // #327 integration seam: opening an asset card navigates to the browse
  // route with the asset id in the bookmarkable `?artifact=` search param
  // (the same param Artifacts.jsx already gates ArtifactPanel on). Sibling
  // #328 rewrites that view to resolve the id to a persisted asset and
  // render real content; today it falls through to a closed panel, which
  // is harmless. Keeping the coupling URL-based (not a shared import)
  // means #328's concurrent panel rewrite can't break this side.
  function openAsset(asset) {
    navigate(`/app/artifacts?artifact=${encodeURIComponent(asset.asset_id)}`);
  }

  // A standalone asset renders inline when its kind can sensibly render in
  // the flow, else as a compact AssetCard (the fallback — #360 decision #2
  // revised). Every predicate keys off `kind` (+ `mime`), NEVER `origin`
  // (#363 owner requirement), so an uploaded CSV/markdown/image renders
  // exactly like a generated one:
  //   - #361 image → responsive inline `<img>` (raster, ≤ 5 MB);
  //   - #363 data  → capped inline CSV table (text/CSV);
  //   - #363 document → capped inline markdown preview (text/markdown);
  //   - everything else — PDFs, xlsx, over-threshold / non-raster images,
  //     diagrams, and any code asset without a locatable fence — cards.
  // Applied to both the user-turn and assistant-turn branches below so
  // upload-origin assets (grouped under the user turn) render inline too.
  function renderStandalone(a) {
    if (isInlineImage(a)) {
      return <InlineImage key={a.asset_id} asset={a} onOpen={openAsset} />;
    }
    if (isInlineData(a)) {
      return <InlineData key={a.asset_id} asset={a} onOpen={openAsset} />;
    }
    if (isInlineDocument(a)) {
      return <InlineDocument key={a.asset_id} asset={a} onOpen={openAsset} />;
    }
    return <AssetCard key={a.asset_id} asset={a} onOpen={openAsset} />;
  }

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
          {hasOlder && (
            <button
              type="button"
              className="load-earlier"
              onClick={handleLoadEarlier}
            >
              Load earlier messages
            </button>
          )}
          {turns.map((t, i) => {
            const isLast = i === turns.length - 1;
            // Retry icon is the canonical regenerate trigger. It's the
            // last assistant turn's responsibility; older turns get a
            // no-op so the row layout stays consistent.
            const retryEnabled =
              isLast && t.role === "assistant" && !t.streaming;
            // Only the last settled assistant turn is retryable; older
            // turns attach `undefined` to the (disabled) Retry button.
            const onRetry = retryEnabled ? () => regenerate({}) : undefined;
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
                {standaloneAssets(t.assets, t.text).map(renderStandalone)}
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
                <div className="msg">
                  {renderMarkdown(t.text, t.streaming, {
                    codeAssets: codeAssetsByFence(t.assets, t.text),
                    onOpenAsset: openAsset,
                  })}
                </div>
                {standaloneAssets(t.assets, t.text).map(renderStandalone)}
                {t.toolSteps && t.toolSteps.length > 0 && (
                  <ToolStepList
                    steps={t.toolSteps}
                    expanded={expandedSteps.get(t.toolSteps[0].toolUseId) ?? false}
                    onToggle={() => toggleStepsExpanded(t.toolSteps[0].toolUseId)}
                  />
                )}
                {t.streamError && (
                  <div className="stream-error" role="alert">
                    <span className="stream-error-msg">
                      Couldn&apos;t complete reply — {t.streamError.message}
                    </span>
                    {/* Retry only when the failure is actually retryable —
                        bedrock_validation ("message too large") would fail
                        identically on resend. `!== false` treats a missing
                        flag as retryable (defensive for frame shapes that
                        predate the field). */}
                    {retryEnabled && t.streamError.retryable !== false && (
                      <button
                        type="button"
                        className="stream-error-retry"
                        onClick={onRetry}
                      >
                        <Icon name="refresh" size={14} />
                        Retry
                      </button>
                    )}
                  </div>
                )}
                {!t.streaming && !t.streamError && (
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
          mcpServers={mcpServers}
          mcpSettings={mcpSettings}
          setMcpSettings={handleMcpChange}
        />
      </div>
    </>
  );
}
