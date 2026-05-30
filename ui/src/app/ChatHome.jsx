// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { parseToken, TOKEN_KEY } from "../lib/auth.js";
import ChannelMark from "../components/ChannelMark.jsx";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
import Composer from "./Composer.jsx";
import { MODELS, QUICK_ACTIONS } from "./data.js";

/**
 * Empty-state shown at /app when there's no active conversation.
 * Greeting + Composer + quick-action chips. Translated from
 * design-sources/app/chat.jsx `Home` function. The composer's onSend is
 * wired to a Phase-6c no-op; mock streaming arrives in 6d.
 */
export default function ChatHome() {
  const prefs = useChannelPrefs();
  const token = localStorage.getItem(TOKEN_KEY) ?? "";
  const claims = parseToken(token) ?? {};
  const userName = (claims.email ?? "you@example.com").split("@")[0] || "You";

  // Derive the model object from the prefs string id.
  const modelObj = MODELS.find((m) => m.id === prefs.model) ?? MODELS[0];
  const setModelObj = (m) => prefs.setModel(m.id);

  // Phase 6c no-op send. Phase 6d swaps this for the mock streamer hook.
  function noOpSend(/* text, atts */) {
    /* no-op until Phase 6d */
  }

  return (
    <div className="home">
      <div className="greet">
        <span className="gm"><ChannelMark size={34} /></span>
        <h1>Back at it, {userName}</h1>
      </div>
      <Composer
        model={modelObj}
        effort={prefs.effort}
        setModel={setModelObj}
        setEffort={prefs.setEffort}
        onSend={noOpSend}
        autofocus
      />
      <div className="quick">
        {QUICK_ACTIONS.map((q) => (
          <button
            type="button"
            className="qa"
            key={q.id}
            onClick={() => noOpSend(`Help me ${q.label.toLowerCase()} something.`, [])}
          >
            <span className="ic"><Icon name={q.icon} size={17} /></span>
            {q.label}
          </button>
        ))}
      </div>
    </div>
  );
}
