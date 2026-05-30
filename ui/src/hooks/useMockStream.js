// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useRef, useState } from "react";
import { SAMPLE_REPLY, SAMPLE_USER } from "../app/data.js";

const HARDCODED_ARTIFACT = {
  title: "ingestion-buffer.ts",
  kind: "Code · 64 lines",
  ic: "code",
};

// Target total duration for the canned stream. The interpolation below
// converts elapsed wall-clock time into a target character index, so the
// reply finishes in roughly this many milliseconds regardless of the
// host frame rate.
const STREAM_DURATION_MS = 5500;

/**
 * Mock streaming engine for the chat conversation. Originally a port of
 * `useStream` from design-sources/app/chat.jsx, now refactored to drive
 * the reveal with `requestAnimationFrame` + time-based interpolation
 * instead of `setInterval`. Reasons:
 *
 *   - `setInterval(fn, 22ms)` fires ~45 times/sec but the browser paints
 *     at 60fps. The cadence mismatch made some frames carry one update
 *     and others none — visible as a stutter even though both rates
 *     were "fast enough on paper".
 *   - rAF aligns each state update with the next paint, so the user sees
 *     at most one update per frame, never two stacking.
 *   - Time-based interpolation (chars revealed ∝ elapsed/TOTAL) keeps the
 *     reveal smooth regardless of how many ticks we hit per second; a
 *     missed frame just lands a slightly larger slice next frame.
 *
 * Real Bedrock streaming will later swap this hook for one with the same
 * `{ turns, send, clear, loadSample }` shape — `Conversation` consumes
 * only this API.
 */
export function useMockStream() {
  const [turns, setTurns] = useState([]);
  // Holds either a requestAnimationFrame id (for active streams) or null.
  const rafRef = useRef(null);

  const stop = useCallback(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
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
      stop();
      const startedAt = performance.now();
      let lastChunkLength = -1;
      const total = SAMPLE_REPLY.length;
      const tick = () => {
        const elapsed = performance.now() - startedAt;
        const progress = Math.min(1, elapsed / STREAM_DURATION_MS);
        const targetLen = Math.floor(progress * total);
        // Skip the React update when two consecutive frames land on the
        // same character count. At 60fps over ~5.5s every frame advances
        // by ~2-3 chars so the equal case is rare in practice; we still
        // gate to avoid spurious renders if the host frame rate spikes.
        /* v8 ignore next */
        if (targetLen !== lastChunkLength) {
          lastChunkLength = targetLen;
          const chunk = SAMPLE_REPLY.slice(0, targetLen);
          setTurns((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = { ...last, text: chunk };
            return next;
          });
        }
        if (progress >= 1) {
          rafRef.current = null;
          setTurns((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = {
              ...last,
              text: SAMPLE_REPLY,
              streaming: false,
              artifact: HARDCODED_ARTIFACT,
            };
            return next;
          });
          return;
        }
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
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
