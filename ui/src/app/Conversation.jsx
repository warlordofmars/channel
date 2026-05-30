// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef } from "react";
import { useParams } from "react-router-dom";
import ChannelMark from "../components/ChannelMark.jsx";
import Composer from "./Composer.jsx";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
import { useMockStream } from "../hooks/useMockStream.js";
import { renderMarkdown } from "./renderMarkdown.jsx";
import { MODELS, RECENTS } from "./data.js";

const PENDING_KEY = "channel-pending-send";

function resolveModel(modelId) {
  return MODELS.find((m) => m.id === modelId) ?? MODELS[0];
}

function consumePendingSend() {
  let raw;
  try {
    raw = sessionStorage.getItem(PENDING_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* private mode etc — best-effort */
  }
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Streaming conversation pane. Translated from
 * design-sources/app/chat.jsx `Conversation`. The URL `:id` decides what
 * happens on mount AND on route-param change:
 *
 *   - id === "new"  → consume sessionStorage[channel-pending-send] and
 *                     kick off send() once (ChatHome handed us a draft).
 *   - id ∈ RECENTS  → loadSample(title, model) — canned SAMPLE_USER +
 *                     SAMPLE_REPLY pair instantly.
 *   - otherwise     → empty conversation.
 *
 * `kickedIdRef` tracks the most recent id we acted on, so:
 *   • StrictMode's double-effect on mount short-circuits on the second fire
 *     (kickedIdRef.current already equals the id).
 *   • Clicking another sidebar Recent while still mounted (id changes from
 *     r1 → r5) DOES re-fire because kickedIdRef.current !== new id.
 *
 * Message-actions row (copy/retry/thumbs) is rendered per assistant turn
 * once that turn is no longer streaming. Buttons are no-ops at Phase 6d.
 *
 * A persistent Composer sits at the bottom of the pane so the user can
 * follow up; calling its onSend appends another user turn + a fresh
 * streaming assistant turn via useMockStream.send.
 */
export default function Conversation() {
  const { id } = useParams();
  const { turns, send, loadSample } = useMockStream();
  const prefs = useChannelPrefs();
  const ref = useRef(null);
  const kickedIdRef = useRef(null);

  // Auto-scroll to the bottom on any turns change (catches each stream tick).
  useEffect(() => {
    const el = ref.current;
    el.scrollTop = el.scrollHeight;
  }, [turns]);

  useEffect(() => {
    if (kickedIdRef.current === id) return;
    kickedIdRef.current = id;
    if (id === "new") {
      const pending = consumePendingSend();
      if (!pending) return;
      const model = resolveModel(pending.modelId);
      send(pending.text, pending.atts || [], model, pending.effort);
      return;
    }
    const recent = RECENTS.find((r) => r.id === id);
    if (recent) {
      loadSample(recent.title, MODELS[0]);
    }
  }, [id, send, loadSample]);

  const modelObj = resolveModel(prefs.model);
  const setModelObj = (m) => prefs.setModel(m.id);
  const followUp = (text, atts) => send(text, atts, modelObj, prefs.effort);
  const noop = () => {};

  return (
    <>
      <div className="convo" ref={ref}>
        <div className="convo-inner">
          {turns.map((t, i) =>
            t.role === "user" ? (
              <div className="turn user" key={i}>
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
              <div className="turn" key={i}>
                <div className="assistant-head">
                  <ChannelMark size={20} />
                  <span className="nm">Channel</span>
                  <span className="mdl">{t.model}</span>
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
                    <button type="button" className="icon-btn" title="Retry" onClick={noop}>
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
              </div>
            )
          )}
        </div>
      </div>
      <Composer
        model={modelObj}
        effort={prefs.effort}
        setModel={setModelObj}
        setEffort={prefs.setEffort}
        onSend={followUp}
        placeholder="Reply…"
      />
    </>
  );
}
