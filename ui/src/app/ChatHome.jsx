// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { useNavigate } from "react-router-dom";
import { parseToken, TOKEN_KEY } from "../lib/auth.js";
import ChannelMark from "../components/ChannelMark.jsx";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
import { useChats } from "../hooks/ChatsContext.jsx";
import Composer from "./Composer.jsx";
import { MODELS, QUICK_ACTIONS } from "./data.js";

/**
 * Empty-state shown at /app when there's no active conversation.
 * Greeting + Composer + quick-action chips. Translated from
 * design-sources/app/chat.jsx `Home` function. Submitting the composer
 * calls `createChat()` on the shared ChatsContext to mint a real chat
 * row, then navigates to `/app/c/{chat_id}` with the first message in
 * route state. Conversation reads `state.firstMessage` on mount and
 * calls `useChatStream.send(...)` to kick off the first turn.
 */
export default function ChatHome() {
  const prefs = useChannelPrefs();
  const { createChat } = useChats();
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

  // Create a real chat row up-front, then route to /app/c/{chat_id} with
  // the first user message in route state. Conversation picks up
  // `state.firstMessage` on mount and calls useChatStream.send(...) to
  // kick off the first turn.
  async function send(text, atts) {
    const chat = await createChat({ modelDefault: modelObj.id });
    navigate(`/app/c/${chat.chat_id}`, {
      state: {
        firstMessage: {
          message: text,
          model: modelObj,
          effort: prefs.effort,
          attachments: atts,
        },
      },
    });
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
