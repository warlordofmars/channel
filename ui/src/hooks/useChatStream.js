// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import { makeSseDecoder } from "../lib/sseParser.js";

// #181 PR-3: in-place patch of one toolStep on the in-flight assistant
// turn. Returns the ORIGINAL `turns` reference when no turn matches
// tempAsstId or no step matches toolUseId (e.g. progress/finished/error
// arriving without a preceding tool_started — no-op rather than crash).
// Reference-equality short-circuit lets React's useState setter no-op
// (skip re-render) for stale / unknown tool_use_id events.
function patchToolStep(turns, tempAsstId, toolUseId, patch) {
  let mutated = false;
  const next = turns.map((t) => {
    if (t.msg_id !== tempAsstId || !t.toolSteps) return t;
    const idx = t.toolSteps.findIndex((s) => s.toolUseId === toolUseId);
    if (idx === -1) return t;
    const nextSteps = [...t.toolSteps];
    nextSteps[idx] = { ...nextSteps[idx], ...patch };
    mutated = true;
    return { ...t, toolSteps: nextSteps };
  });
  return mutated ? next : turns;
}

/**
 * Real SSE-driven chat hook. Loads chat history on mount, optimistically
 * renders new turns on send, parses streamed deltas, and finalises turns
 * on the done event.
 *
 * Lifecycle states exposed via `status`:
 *   - "idle"             — no in-flight work
 *   - "loading-history"  — initial GET /api/chats/{id} in flight
 *   - "streaming"        — POST /api/chats/{id}/messages SSE active
 *   - "error"            — last history-load or send failed; `error`
 *                          holds the Error
 *
 * Returns `{ turns, send, abort, status, error }`. `turns` is an array
 * of `{ msg_id, role, text, ...flags }`; the assistant turn carries
 * `streaming: true` until the `done` event arrives.
 *
 * @param {string|null} chatId
 * @param {object} [options]
 * @param {(title: string) => void} [options.onTitleSuggested] — Phase 7d
 *   auto-title callback. Fired when the backend emits a
 *   ``title_suggested`` SSE event (server-side after the first round-trip
 *   for fresh chats). Default no-op so existing callers don't break.
 */
export function useChatStream(chatId, { onTitleSuggested } = {}) {
  const [turns, setTurns] = useState([]);
  const [status, setStatus] = useState(chatId ? "loading-history" : "idle");
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  // Tracks the chatId of the most-recently-applied history install.
  // Used to distinguish a real chatId change (user clicked a different
  // chat in the sidebar — clear stale turns) from React StrictMode dev
  // mode's intentional double-effect (cleanup → re-setup with the SAME
  // chatId — must NOT clear, otherwise we wipe whatever optimistic
  // turns send() just pushed and the SSE deltas land on nothing).
  const loadedChatIdRef = useRef(null);
  useEffect(() => {
    if (!chatId) {
      setTurns([]);
      setStatus("idle");
      loadedChatIdRef.current = null;
      return;
    }
    if (
      loadedChatIdRef.current !== null &&
      loadedChatIdRef.current !== chatId
    ) {
      setTurns([]);
    }
    loadedChatIdRef.current = chatId;
    let cancelled = false;
    setStatus("loading-history");
    api
      .getChat(chatId)
      .then(({ messages }) => {
        if (cancelled) return;
        // Only install loaded history if the caller hasn't already
        // pushed optimistic turns (the first-message-creates-chat flow
        // calls send() during the same mount).
        setTurns((prev) => (prev.length === 0 ? messages : prev));
        setStatus("idle");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err);
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [chatId]);

  // Abort the previous chat's in-flight stream when chatId actually
  // changes. We can't use a `useEffect(() => () => abortRef.current?.
  // abort(), [chatId])` cleanup because React StrictMode dev mode
  // double-invokes effect cleanups during the initial mount — which
  // would immediately abort the first send() the moment it fires. We
  // track the previous chatId in a ref and abort only on real change.
  const prevChatIdRef = useRef(chatId);
  if (prevChatIdRef.current !== chatId) {
    abortRef.current?.abort();
    prevChatIdRef.current = chatId;
  }

  // Hook-local SSE reader loop. Shared by send() and regenerate().
  //
  // tempUserId is optional: regenerate doesn't add a temp user row (the
  // backend re-streams only the assistant turn and never emits
  // user_persisted). When tempUserId is null, a user_persisted event
  // (should it arrive anyway) is a no-op because no row matches.
  //
  // try/catch wraps the loop because AbortController.abort() (from
  // chatId change or unmount) causes reader.read() to reject. The
  // abort is user-initiated so we bail out silently — state remains
  // in whatever partial form it reached.
  async function readSse(response, tempAsstId, tempUserId) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const sse = makeSseDecoder();
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const events = sse.feed(decoder.decode(value, { stream: true }));
        for (const event of events) {
          if (event.type === "user_persisted") {
            setTurns((prev) =>
              prev.map((t) =>
                t.msg_id === tempUserId
                  ? { ...t, msg_id: event.msg_id, pending: false }
                  : t,
              ),
            );
          } else if (event.type === "delta") {
            setTurns((prev) =>
              prev.map((t) =>
                t.msg_id === tempAsstId
                  ? { ...t, text: t.text + event.text }
                  : t,
              ),
            );
          } else if (event.type === "done") {
            setTurns((prev) =>
              prev.map((t) =>
                t.msg_id === tempAsstId
                  ? {
                      ...t,
                      msg_id: event.msg_id,
                      streaming: false,
                      model: event.model,
                      input_tokens: event.input_tokens,
                      output_tokens: event.output_tokens,
                    }
                  : t,
              ),
            );
            setStatus("idle");
          } else if (event.type === "title_suggested") {
            // Phase 7d: backend emits this after the first round-trip
            // on a fresh chat. Caller decides what to do with the title
            // (typically: update the sidebar via ChatsContext.renameChat).
            onTitleSuggested?.(event.title);
          } else if (event.type === "follow_ups_suggested") {
            // Phase 113: attach the suggestion list to the matching
            // assistant turn so the Conversation view can render a
            // chip row below it. The event arrives after `done`, so
            // the temp id has already been swapped to event.message_id
            // by that branch above.
            setTurns((prev) =>
              prev.map((t) =>
                t.msg_id === event.message_id
                  ? { ...t, followUps: event.suggestions }
                  : t,
              ),
            );
          } else if (event.type === "tool_started") {
            // #181 PR-2: push a new running step onto the in-flight
            // assistant turn's toolSteps array. The active assistant
            // turn is the one matching tempAsstId (set when send /
            // regenerate seeded the streaming row).
            setTurns((prev) =>
              prev.map((t) => {
                if (t.msg_id !== tempAsstId) return t;
                const steps = [...(t.toolSteps || [])];
                steps.push({
                  toolUseId: event.tool_use_id,
                  toolName: event.tool_name,
                  argsPreview: event.args_preview,
                  statusText: null,
                  summary: null,
                  errorType: null,
                  partialResultCount: 0,
                  status: "running",
                });
                return { ...t, toolSteps: steps };
              }),
            );
          } else if (event.type === "tool_progress") {
            setTurns((prev) =>
              patchToolStep(prev, tempAsstId, event.tool_use_id, {
                statusText: event.status_text,
              }),
            );
          } else if (event.type === "tool_finished") {
            setTurns((prev) =>
              patchToolStep(prev, tempAsstId, event.tool_use_id, {
                summary: event.summary,
                status: "finished",
              }),
            );
          } else if (event.type === "tool_error") {
            setTurns((prev) =>
              patchToolStep(prev, tempAsstId, event.tool_use_id, {
                errorType: event.error_type,
                partialResultCount: event.partial_result_count,
                status: "error",
              }),
            );
          }
        }
      }
    } catch {
      // Reader aborted or errored — bail out silently. Abort is
      // user-initiated (chatId change / unmount / explicit abort()),
      // so no error surface needed.
    }
  }

  const send = useCallback(
    async ({ message, model, effort, attachments }) => {
      if (!chatId) return;
      setError(null);
      const tempUserId = `tmp-u-${crypto.randomUUID()}`;
      const tempAsstId = `tmp-a-${crypto.randomUUID()}`;
      setTurns((prev) => [
        ...prev,
        { msg_id: tempUserId, role: "user", text: message, pending: true },
        { msg_id: tempAsstId, role: "assistant", text: "", streaming: true },
      ]);
      setStatus("streaming");

      const controller = new AbortController();
      abortRef.current = controller;

      let response;
      try {
        response = await api.streamMessage(chatId, {
          message,
          model,
          effort,
          attachments,
          idempotencyKey: crypto.randomUUID(),
          signal: controller.signal,
        });
      } catch (err) {
        setError(err);
        setStatus("error");
        return;
      }

      await readSse(response, tempAsstId, tempUserId);
    },
    [chatId],
  );

  const regenerate = useCallback(
    async ({ model, effort } = {}) => {
      if (!chatId) return;
      setError(null);

      // Drop the last assistant turn locally (if any); add an empty
      // streaming row in its place. The backend re-streams only the
      // assistant turn — the user turn stays put.
      const tempAsstId = `tmp-a-${crypto.randomUUID()}`;
      setTurns((prev) => {
        const next =
          prev[prev.length - 1]?.role === "assistant"
            ? prev.slice(0, -1)
            : [...prev];
        return [
          ...next,
          { msg_id: tempAsstId, role: "assistant", text: "", streaming: true },
        ];
      });
      setStatus("streaming");

      const controller = new AbortController();
      abortRef.current = controller;

      let response;
      try {
        response = await api.regenerate(chatId, {
          model,
          effort,
          signal: controller.signal,
        });
      } catch (err) {
        setError(err);
        setStatus("error");
        return;
      }

      await readSse(response, tempAsstId, null);
    },
    [chatId],
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { turns, send, regenerate, abort, status, error };
}
