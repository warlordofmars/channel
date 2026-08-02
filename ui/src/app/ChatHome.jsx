// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { parseToken, readToken } from "../lib/auth.js";
import ChannelMark from "../components/ChannelMark.jsx";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
import { useChats } from "../hooks/ChatsContext.jsx";
import Composer from "./Composer.jsx";
import {
  QUICK_ACTIONS,
  cachedModels,
  loadModels,
  mergeWithDisplayMeta,
} from "./data.js";

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
  const token = readToken();
  const claims = parseToken(token) ?? {};
  // Prefer the Google display_name's first word ("John Carter" → "John");
  // fall back to the email's local-part, then to a generic "You".
  const firstName =
    (claims.display_name && claims.display_name.trim().split(/\s+/)[0]) ||
    (claims.email ?? "you@example.com").split("@")[0] ||
    "You";

  // The model allowlist comes from /api/models (issue #148 dropped the
  // hardcoded fallback). Until it lands we render with the prefs.model
  // id wrapped in display meta — the Composer can still submit on the
  // raw id while the picker shows "Loading models…".
  const [models, setModels] = useState(() => cachedModels());

  useEffect(function fetchModelsOnMount() {
    // `loadModels()` is internally cached, so a hot mount after another
    // consumer fetched is a no-op. We still call it so the cache is
    // populated for the next consumer if we're the first mount.
    loadModels()
      .then(setModels)
      .catch(function onModelsFetchError() { setModels([]); });
  }, []);

  // Derive the model object from the prefs string id. Use the loaded
  // allowlist when present; otherwise wrap the id with the local
  // display-meta map so the Composer's send path still has a usable
  // `modelObj.id`.
  const modelObj =
    (models && models.find((m) => m.id === prefs.model)) ||
    (models && models[0]) ||
    mergeWithDisplayMeta({ id: prefs.model });
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
          // Backend `SendMessageRequest.model` is a short id string, not
          // the client display object. The picker emits the full record,
          // so extract the id here before stashing.
          model: modelObj.id,
          effort: prefs.effort,
          attachments: atts,
        },
      },
    });
  }

  function onQuickActionClick(label) {
    send(`Help me ${label.toLowerCase()} something.`, []);
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
            onClick={() => onQuickActionClick(q.label)}
          >
            <span className="ic"><Icon name={q.icon} size={17} /></span>
            {q.label}
          </button>
        ))}
      </div>
    </div>
  );
}
