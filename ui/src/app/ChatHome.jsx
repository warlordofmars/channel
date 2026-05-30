// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { useNavigate } from "react-router-dom";
import { parseToken, TOKEN_KEY } from "../lib/auth.js";
import ChannelMark from "../components/ChannelMark.jsx";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
import Composer from "./Composer.jsx";
import { MODELS, QUICK_ACTIONS } from "./data.js";

/**
 * Empty-state shown at /app when there's no active conversation.
 * Greeting + Composer + quick-action chips. Translated from
 * design-sources/app/chat.jsx `Home` function. Sending stashes the
 * draft in sessionStorage and navigates to /app/c/new, where
 * Conversation consumes it and kicks off the mock streamer.
 */
export default function ChatHome() {
  const prefs = useChannelPrefs();
  const token = localStorage.getItem(TOKEN_KEY) ?? "";
  const claims = parseToken(token) ?? {};
  // Prefer the Google display_name's first word ("John Carter" → "John");
  // fall back to the email's local-part, then to a generic "You".
  const firstName =
    (claims.display_name && claims.display_name.trim().split(/\s+/)[0]) ||
    (claims.email ?? "you@example.com").split("@")[0] ||
    "You";

  // Derive the model object from the prefs string id.
  const modelObj = MODELS.find((m) => m.id === prefs.model) ?? MODELS[0];
  const setModelObj = (m) => prefs.setModel(m.id);

  const navigate = useNavigate();

  // Hand the draft off to Conversation via sessionStorage and route to
  // /app/c/new. Conversation reads the payload on mount, calls
  // useMockStream.send(...), and clears the key.
  function send(text, atts) {
    try {
      sessionStorage.setItem(
        "channel-pending-send",
        JSON.stringify({
          text,
          atts,
          modelId: modelObj.id,
          effort: prefs.effort,
        })
      );
    } catch {
      /* private mode etc — Conversation just renders empty */
    }
    navigate("/app/c/new");
  }

  return (
    <div className="home">
      <div className="greet">
        <span className="gm"><ChannelMark size={34} /></span>
        <h1>Back at it, {firstName}</h1>
      </div>
      <Composer
        model={modelObj}
        effort={prefs.effort}
        setModel={setModelObj}
        setEffort={prefs.setEffort}
        onSend={send}
        autofocus
      />
      <div className="quick">
        {QUICK_ACTIONS.map((q) => (
          <button
            type="button"
            className="qa"
            key={q.id}
            onClick={() => send(`Help me ${q.label.toLowerCase()} something.`, [])}
          >
            <span className="ic"><Icon name={q.icon} size={17} /></span>
            {q.label}
          </button>
        ))}
      </div>
    </div>
  );
}
