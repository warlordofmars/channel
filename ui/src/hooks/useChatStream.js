// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import { makeSseDecoder } from "../lib/sseParser.js";

/**
 * Real SSE-driven replacement for useMockStream. Loads chat history on
 * mount, optimistically renders new turns on send, parses streamed
 * deltas, and finalises turns on the done event.
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
 */
export function useChatStream(chatId) {
  const [turns, setTurns] = useState([]);
  const [status, setStatus] = useState(chatId ? "loading-history" : "idle");
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  useEffect(() => {
    if (!chatId) {
      setTurns([]);
      setStatus("idle");
      return;
    }
    let cancelled = false;
    setStatus("loading-history");
    api
      .getChat(chatId)
      .then(({ messages }) => {
        if (cancelled) return;
        setTurns(messages);
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

  const send = useCallback(
    async ({ message, model, effort, attachments }) => {
      if (!chatId) return;
      setError(null);
      const tempUserId = `tmp-u-${Date.now()}`;
      const tempAsstId = `tmp-a-${Date.now()}`;
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

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const sse = makeSseDecoder();

      // Reader loop: each chunk feeds the stateful SSE decoder which
      // hands back complete events. The decoder buffers partial events
      // across chunk boundaries so we never JSON.parse a half-line.
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
          }
        }
      }
    },
    [chatId],
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { turns, send, abort, status, error };
}
