// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

vi.mock("../api.js", () => ({
  getChat: vi.fn(),
  streamMessage: vi.fn(),
  regenerate: vi.fn(),
}));

import * as api from "../api.js";
import { useChatStream } from "./useChatStream.js";

// jsdom in vitest 1+ ships crypto.randomUUID, but guard for older envs.
globalThis.crypto ??= { randomUUID: () => "uuid-stub" };
if (!globalThis.crypto.randomUUID) {
  globalThis.crypto.randomUUID = () => "uuid-stub";
}

function makeMockResponseBody(events) {
  // events: array of {type, ...payload}
  const encoder = new TextEncoder();
  const chunks = events.map((e) => `data: ${JSON.stringify(e)}\n\n`);
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(chunks[i]));
      i += 1;
    },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useChatStream", () => {
  it("loads chat history on mount when chatId is set", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [{ msg_id: "m1", role: "user", text: "hello" }],
      next_cursor: null,
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.turns).toEqual([
      { msg_id: "m1", role: "user", text: "hello" },
    ]);
  });

  it("skips history load when chatId is null", () => {
    const { result } = renderHook(() => useChatStream(null));
    expect(result.current.turns).toEqual([]);
    expect(api.getChat).not.toHaveBeenCalled();
  });

  it("streams deltas into the assistant turn", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [],
      next_cursor: null,
    });
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        { type: "user_persisted", msg_id: "user-1", seq: 0 },
        { type: "delta", text: "Hello" },
        { type: "delta", text: " world" },
        {
          type: "done",
          msg_id: "asst-1",
          seq: 1,
          model: "canned-stream-v1",
          input_tokens: 0,
          output_tokens: 0,
          stop_reason: "end_turn",
        },
      ]),
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    expect(result.current.turns).toHaveLength(2);
    expect(result.current.turns[0]).toMatchObject({
      role: "user",
      msg_id: "user-1",
    });
    expect(result.current.turns[1]).toMatchObject({
      role: "assistant",
      text: "Hello world",
      streaming: false,
      msg_id: "asst-1",
    });
  });

  it("sets error status when the server returns non-ok", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [],
      next_cursor: null,
    });
    api.streamMessage.mockRejectedValue(new Error("500"));

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    expect(result.current.status).toBe("error");
    expect(result.current.error).toBeInstanceOf(Error);
  });

  it("sets status='error' when history fetch fails", async () => {
    api.getChat.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("error"));
  });

  it("clears turns and skips history when chatId transitions to null", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [{ msg_id: "m1", role: "user", text: "hello" }],
      next_cursor: null,
    });
    const { result, rerender } = renderHook(({ id }) => useChatStream(id), {
      initialProps: { id: "c1" },
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.turns).toHaveLength(1);

    rerender({ id: null });
    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.turns).toEqual([]);
  });

  it("abort() calls AbortController.abort() on the in-flight send", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [],
      next_cursor: null,
    });
    let capturedSignal = null;
    // Hang the stream so the controller is still active when we abort.
    let resolveStreamCall;
    api.streamMessage.mockImplementation((_id, opts) => {
      capturedSignal = opts.signal;
      return new Promise((resolve) => {
        resolveStreamCall = resolve;
      });
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    // Fire send without awaiting it — the stream is intentionally hanging.
    let sendPromise;
    act(() => {
      sendPromise = result.current.send({
        message: "hi",
        model: "m",
        effort: "med",
      });
    });
    await waitFor(() => expect(capturedSignal).not.toBeNull());
    expect(capturedSignal.aborted).toBe(false);

    act(() => {
      result.current.abort();
    });
    expect(capturedSignal.aborted).toBe(true);

    // Unblock the hanging streamMessage promise so the send() coroutine
    // settles before the test ends. Resolve with a closed body so the
    // read loop exits immediately.
    await act(async () => {
      resolveStreamCall({
        ok: true,
        body: makeMockResponseBody([]),
      });
      await sendPromise;
    });
  });

  it("abort() is a no-op before any send", () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [],
      next_cursor: null,
    });
    const { result } = renderHook(() => useChatStream("c1"));
    // Should not throw.
    expect(() => result.current.abort()).not.toThrow();
  });

  it("ignores send when chatId is null", async () => {
    const { result } = renderHook(() => useChatStream(null));
    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });
    expect(api.streamMessage).not.toHaveBeenCalled();
  });

  it("ignores history fetch resolution after the chatId changes (cancelled .then branch)", async () => {
    // Hold the getChat promise until after we've changed chatId so the
    // .then() runs with cancelled=true (covers the true-branch of the
    // cancelled guard in the resolve path).
    let resolveFirst;
    api.getChat.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );
    api.getChat.mockResolvedValueOnce({
      chat: { chat_id: "c2" },
      messages: [{ msg_id: "m2", role: "user", text: "second" }],
      next_cursor: null,
    });

    const { result, rerender } = renderHook(({ id }) => useChatStream(id), {
      initialProps: { id: "c1" },
    });
    // Swap chatId before the first getChat resolves — the cleanup sets
    // cancelled=true so the late .then() resolution is a no-op.
    rerender({ id: "c2" });
    await act(async () => {
      resolveFirst({
        chat: { chat_id: "c1" },
        messages: [{ msg_id: "stale", role: "user", text: "stale" }],
        next_cursor: null,
      });
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));
    // The "stale" message from the first request must not appear.
    expect(result.current.turns).toEqual([
      { msg_id: "m2", role: "user", text: "second" },
    ]);
  });

  it("ignores history fetch rejection after the chatId changes (cancelled .catch branch)", async () => {
    let rejectFirst;
    api.getChat.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectFirst = reject;
        }),
    );
    api.getChat.mockResolvedValueOnce({
      chat: { chat_id: "c2" },
      messages: [],
      next_cursor: null,
    });

    const { result, rerender } = renderHook(({ id }) => useChatStream(id), {
      initialProps: { id: "c1" },
    });
    rerender({ id: "c2" });
    await act(async () => {
      rejectFirst(new Error("late-failure"));
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));
    // The status must NOT have flipped to "error" — the late rejection
    // belonged to the cancelled effect.
    expect(result.current.status).toBe("idle");
    expect(result.current.error).toBeNull();
  });

  it("silently drops unknown SSE event types (falls through all if/else branches)", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [],
      next_cursor: null,
    });
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        { type: "user_persisted", msg_id: "user-u", seq: 0 },
        { type: "delta", text: "hi" },
        // Unknown event type — should be ignored without crashing
        // (covers the false branch of `else if (event.type === "done")`).
        { type: "telemetry", note: "noop" },
        {
          type: "done",
          msg_id: "asst-u",
          seq: 1,
          model: "canned-stream-v1",
          input_tokens: 0,
          output_tokens: 0,
          stop_reason: "end_turn",
        },
      ]),
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    expect(result.current.status).toBe("idle");
    expect(result.current.turns[1]).toMatchObject({
      role: "assistant",
      text: "hi",
      streaming: false,
      msg_id: "asst-u",
    });
  });

  it("aborts the in-flight stream when chatId changes", async () => {
    // First chat: history loads, then stream starts but doesn't complete.
    api.getChat.mockResolvedValueOnce({
      chat: { chat_id: "c1" },
      messages: [],
      next_cursor: null,
    });

    // Construct a never-completing stream so we can observe abort.
    const neverComplete = new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder();
        controller.enqueue(
          encoder.encode('data: {"type":"delta","text":"x"}\n\n'),
        );
        // Never close.
      },
    });

    let capturedSignal;
    api.streamMessage.mockImplementationOnce((_chatId, opts) => {
      capturedSignal = opts.signal;
      return Promise.resolve({ ok: true, body: neverComplete });
    });

    // Second chat: history fetch only, no streaming.
    api.getChat.mockResolvedValueOnce({
      chat: { chat_id: "c2" },
      messages: [],
      next_cursor: null,
    });

    const { result, rerender } = renderHook(({ id }) => useChatStream(id), {
      initialProps: { id: "c1" },
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));

    // Start the never-completing stream.
    act(() => {
      result.current.send({ message: "hi" });
    });

    // Switch to a different chat while the stream is still in flight.
    rerender({ id: "c2" });

    // The captured signal from the first send should now be aborted.
    await waitFor(() => expect(capturedSignal.aborted).toBe(true));
  });

  // Note: there is no "aborts on unmount" test anymore. The previous
  // implementation aborted via a useEffect cleanup tied to [chatId],
  // which fires during React StrictMode dev double-effect — killing
  // the first send() in normal dev usage. The current implementation
  // only aborts on real chatId change. Real unmount mid-stream is a
  // rare edge case; the browser closes the connection on page nav
  // anyway, and Bedrock streams to completion in tens of seconds at
  // worst. The chatId-change test above still verifies abort works.

  it("leaves unrelated turns untouched while streaming (false-branch of msg_id map)", async () => {
    // Seed history with an existing turn so the per-event setTurns map
    // hits its false branch (existing turn's msg_id !== tempUserId/tempAsstId).
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [{ msg_id: "history-1", role: "user", text: "earlier" }],
      next_cursor: null,
    });
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        { type: "user_persisted", msg_id: "user-2", seq: 0 },
        { type: "delta", text: "ok" },
        {
          type: "done",
          msg_id: "asst-2",
          seq: 1,
          model: "canned-stream-v1",
          input_tokens: 0,
          output_tokens: 0,
          stop_reason: "end_turn",
        },
      ]),
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    expect(result.current.turns).toHaveLength(3);
    expect(result.current.turns[0]).toMatchObject({ msg_id: "history-1" });
    expect(result.current.turns[1]).toMatchObject({
      role: "user",
      msg_id: "user-2",
    });
    expect(result.current.turns[2]).toMatchObject({
      role: "assistant",
      msg_id: "asst-2",
      streaming: false,
      text: "ok",
    });
  });

  it("regenerate drops last assistant turn and re-streams", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [
        { msg_id: "u1", role: "user", text: "hi" },
        { msg_id: "a1", role: "assistant", text: "first reply" },
      ],
      next_cursor: null,
    });
    api.regenerate.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        // Note: NO user_persisted event for regenerate.
        { type: "delta", text: "second" },
        {
          type: "done",
          msg_id: "a2",
          seq: 1,
          model: "anthropic.claude-sonnet-4-6",
          input_tokens: 0,
          output_tokens: 0,
          stop_reason: "end_turn",
        },
      ]),
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.regenerate({ model: "claude-opus-4-6" });
    });

    // User turn UNCHANGED + new assistant turn with the re-streamed text.
    expect(result.current.turns).toHaveLength(2);
    expect(result.current.turns[0]).toMatchObject({
      role: "user",
      msg_id: "u1",
    });
    expect(result.current.turns[1]).toMatchObject({
      role: "assistant",
      text: "second",
      streaming: false,
    });
    expect(api.regenerate).toHaveBeenCalledWith(
      "c1",
      expect.objectContaining({ model: "claude-opus-4-6" }),
    );
  });

  it("regenerate handles error from api.regenerate", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [],
      next_cursor: null,
    });
    api.regenerate.mockRejectedValue(new Error("500"));

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.regenerate({});
    });

    expect(result.current.status).toBe("error");
  });

  it("regenerate does nothing when chatId is null", async () => {
    const { result } = renderHook(() => useChatStream(null));
    await act(async () => {
      await result.current.regenerate({});
    });
    expect(api.regenerate).not.toHaveBeenCalled();
  });

  it("regenerate handles empty turns (no assistant to drop)", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [],
      next_cursor: null,
    });
    api.regenerate.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        { type: "delta", text: "ok" },
        {
          type: "done",
          msg_id: "a1",
          seq: 1,
          model: "m",
          input_tokens: 0,
          output_tokens: 0,
          stop_reason: "end_turn",
        },
      ]),
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.regenerate({});
    });

    // The empty-array branch in `prev.slice(0, -1)` should just be a
    // no-op for the "no assistant" case — only the new empty streaming
    // row is added.
    expect(result.current.turns).toHaveLength(1);
    expect(result.current.turns[0].role).toBe("assistant");
  });
});
