// Copyright (c) 2026 John Carter. All rights reserved.
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMockStream } from "./useMockStream.js";
import { MODELS, SAMPLE_REPLY, SAMPLE_USER } from "../app/data.js";

const OPUS = MODELS[0]; // claude-opus-4-8
const EFFORT = "High";

describe("useMockStream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts with an empty turns array", () => {
    const { result } = renderHook(() => useMockStream());
    expect(result.current.turns).toEqual([]);
  });

  it("send() appends a user turn + an empty streaming assistant turn immediately", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.send("hi", [], OPUS, EFFORT));
    expect(result.current.turns.length).toBe(2);
    expect(result.current.turns[0]).toMatchObject({ role: "user", text: "hi", atts: [] });
    expect(result.current.turns[1]).toMatchObject({
      role: "assistant",
      text: "",
      streaming: true,
      model: `${OPUS.name} · ${EFFORT}`,
    });
  });

  it("send() reveals SAMPLE_REPLY progressively over ~5.5s via requestAnimationFrame", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.send("hi", [], OPUS, EFFORT));
    // After a handful of frames, some prefix of SAMPLE_REPLY is visible.
    act(() => vi.advanceTimersByTime(120));
    const partial = result.current.turns[1].text;
    expect(partial.length).toBeGreaterThan(0);
    expect(SAMPLE_REPLY.startsWith(partial)).toBe(true);
    expect(partial.length).toBeLessThan(SAMPLE_REPLY.length);
    expect(result.current.turns[1].streaming).toBe(true);

    // After the full 5.5s duration the reply is complete + the artifact lands.
    act(() => vi.advanceTimersByTime(6000));
    expect(result.current.turns[1].text).toBe(SAMPLE_REPLY);
    expect(result.current.turns[1].streaming).toBe(false);
    expect(result.current.turns[1].artifact).toEqual({
      title: "ingestion-buffer.ts",
      kind: "Code · 64 lines",
      ic: "code",
    });
  });

  it("clear() drops all turns and stops any in-flight stream", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.send("hi", [], OPUS, EFFORT));
    act(() => vi.advanceTimersByTime(38)); // partway through
    act(() => result.current.clear());
    expect(result.current.turns).toEqual([]);
    // Advancing more time should NOT bring turns back.
    act(() => vi.advanceTimersByTime(38 * 200));
    expect(result.current.turns).toEqual([]);
  });

  it("loadSample(title, model) populates a finished conversation in one tick", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.loadSample("Some recent", OPUS));
    expect(result.current.turns.length).toBe(2);
    expect(result.current.turns[0]).toMatchObject({ role: "user", text: SAMPLE_USER, atts: [] });
    expect(result.current.turns[1]).toMatchObject({
      role: "assistant",
      text: SAMPLE_REPLY,
      streaming: false,
      model: `${OPUS.name} · High`,
      artifact: { title: "ingestion-buffer.ts", kind: "Code · 64 lines", ic: "code" },
    });
  });

  it("loadSample() defaults the model label to 'Claude Opus 4.8 · High' when no model is passed", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.loadSample("Some recent"));
    expect(result.current.turns[1].model).toBe("Claude Opus 4.8 · High");
  });

  it("loadSample() interrupts an in-flight stream", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.send("hi", [], OPUS, EFFORT));
    act(() => vi.advanceTimersByTime(38));
    act(() => result.current.loadSample("Some recent", OPUS));
    // Should be the canned conversation now, with no streaming flag.
    expect(result.current.turns[1].streaming).toBe(false);
    expect(result.current.turns[0].text).toBe(SAMPLE_USER);
    // And advancing time should not re-trigger the old stream.
    act(() => vi.advanceTimersByTime(38 * 200));
    expect(result.current.turns[1].text).toBe(SAMPLE_REPLY);
  });

  it("unmounting clears any active timer (no stray state updates)", () => {
    const { result, unmount } = renderHook(() => useMockStream());
    act(() => result.current.send("hi", [], OPUS, EFFORT));
    act(() => vi.advanceTimersByTime(38));
    unmount();
    // If the timer leaked we'd see a React act() warning. As a positive
    // assertion, we just check no error is thrown when the clock moves.
    expect(() => vi.advanceTimersByTime(38 * 200)).not.toThrow();
  });

  it("send() defaults atts to [] when undefined is passed", () => {
    const { result } = renderHook(() => useMockStream());
    act(() => result.current.send("hi", undefined, OPUS, EFFORT));
    expect(result.current.turns[0].atts).toEqual([]);
  });
});
