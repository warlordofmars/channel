// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useRef, useState } from "react";
import { SAMPLE_REPLY, SAMPLE_USER } from "../app/data.js";

const HARDCODED_ARTIFACT = {
  title: "ingestion-buffer.ts",
  kind: "Code · 64 lines",
  ic: "code",
};

/**
 * Mock streaming engine for the chat conversation. Ported verbatim from
 * design-sources/app/chat.jsx `useStream`. Real Bedrock streaming will
 * swap this hook for one with the same `{ turns, send, clear, loadSample }`
 * shape so the components that consume it don't have to change.
 *
 * `send(text, atts, model, effort)` pushes a user turn + an empty streaming
 * assistant turn, then advances 2 words every 38ms from SAMPLE_REPLY until
 * fully drained. The assistant turn gets a hardcoded artifact when done.
 *
 * `loadSample(title, model)` populates a finished conversation immediately
 * — used when the user clicks a Recent in the sidebar.
 */
export function useMockStream() {
  const [turns, setTurns] = useState([]);
  const timer = useRef(null);

  const stop = useCallback(() => {
    if (timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
  }, []);

  const send = useCallback(
    (text, atts, model, effort) => {
      setTurns((prev) => [
        ...prev,
        { role: "user", text, atts: atts || [] },
        {
          role: "assistant",
          text: "",
          model: `${model.name} · ${effort}`,
          streaming: true,
        },
      ]);
      const words = SAMPLE_REPLY.split(/(\s+)/);
      let i = 0;
      stop();
      timer.current = setInterval(() => {
        i += 2;
        const chunk = words.slice(0, i).join("");
        setTurns((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, text: chunk };
          return next;
        });
        if (i >= words.length) {
          stop();
          setTurns((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = {
              ...last,
              streaming: false,
              artifact: HARDCODED_ARTIFACT,
            };
            return next;
          });
        }
      }, 38);
    },
    [stop]
  );

  const clear = useCallback(() => {
    stop();
    setTurns([]);
  }, [stop]);

  const loadSample = useCallback(
    (_title, model) => {
      stop();
      const modelLabel = `${model ? model.name : "Claude Opus 4.8"} · High`;
      setTurns([
        { role: "user", text: SAMPLE_USER, atts: [] },
        {
          role: "assistant",
          text: SAMPLE_REPLY,
          model: modelLabel,
          streaming: false,
          artifact: HARDCODED_ARTIFACT,
        },
      ]);
    },
    [stop]
  );

  useEffect(() => stop, [stop]);

  return { turns, send, clear, loadSample };
}
