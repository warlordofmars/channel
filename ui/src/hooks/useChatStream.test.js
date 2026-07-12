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

// Build a getChat response page. Keeps the #270 backward-pagination
// tests from repeating the same {chat, messages, older_cursor} literal.
function chatPage(messages, olderCursor = null, chatId = "c1") {
  return { chat: { chat_id: chatId }, messages, older_cursor: olderCursor };
}

// Mount the hook for a chat whose initial page is already queued on the
// getChat mock, and wait for history load to settle.
async function mountLoaded(id = "c1") {
  const view = renderHook(({ id }) => useChatStream(id), {
    initialProps: { id },
  });
  await waitFor(() => expect(view.result.current.status).toBe("idle"));
  return view;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useChatStream", () => {
  it("loads chat history on mount when chatId is set", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [{ msg_id: "m1", role: "user", text: "hello" }],
      older_cursor: null,
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
      older_cursor: null,
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
      older_cursor: null,
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
      older_cursor: null,
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
      older_cursor: null,
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
      older_cursor: null,
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
      older_cursor: null,
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
        older_cursor: null,
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
      older_cursor: null,
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
      older_cursor: null,
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

  it("forwards title_suggested events to onTitleSuggested callback", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [],
      older_cursor: null,
    });
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        { type: "user_persisted", msg_id: "u-1", seq: 0 },
        { type: "delta", text: "Hi" },
        {
          type: "done",
          msg_id: "a-1",
          seq: 1,
          model: "x",
          input_tokens: 0,
          output_tokens: 0,
          stop_reason: "end_turn",
        },
        { type: "title_suggested", chat_id: "c1", title: "Fresh title" },
      ]),
    });

    const onTitleSuggested = vi.fn();
    const { result } = renderHook(() =>
      useChatStream("c1", { onTitleSuggested }),
    );
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    expect(onTitleSuggested).toHaveBeenCalledWith("Fresh title");
  });

  it("does not crash when title_suggested arrives without onTitleSuggested option", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [],
      older_cursor: null,
    });
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        { type: "done", msg_id: "a-1", seq: 1, model: "x", input_tokens: 0, output_tokens: 0, stop_reason: "end_turn" },
        { type: "title_suggested", chat_id: "c1", title: "Title without callback" },
      ]),
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    // No throw — and the stream still completes normally.
    expect(result.current.status).toBe("idle");
  });

  it("attaches follow-up suggestions to the matching assistant turn", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [],
      older_cursor: null,
    });
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        { type: "user_persisted", msg_id: "u-1", seq: 0 },
        { type: "delta", text: "Reply" },
        {
          type: "done",
          msg_id: "a-1",
          seq: 1,
          model: "x",
          input_tokens: 0,
          output_tokens: 0,
          stop_reason: "end_turn",
        },
        {
          type: "follow_ups_suggested",
          chat_id: "c1",
          message_id: "a-1",
          suggestions: ["One", "Two", "Three"],
        },
      ]),
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    const assistant = result.current.turns.find((t) => t.msg_id === "a-1");
    expect(assistant).toBeDefined();
    expect(assistant.followUps).toEqual(["One", "Two", "Three"]);
  });

  it("ignores follow_ups_suggested when no turn matches the message_id", async () => {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: [],
      older_cursor: null,
    });
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        {
          type: "done",
          msg_id: "a-1",
          seq: 1,
          model: "x",
          input_tokens: 0,
          output_tokens: 0,
          stop_reason: "end_turn",
        },
        {
          type: "follow_ups_suggested",
          chat_id: "c1",
          message_id: "no-such-turn",
          suggestions: ["Nope"],
        },
      ]),
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    // No turn should have followUps populated.
    for (const t of result.current.turns) {
      expect(t.followUps).toBeUndefined();
    }
  });

  it("aborts the in-flight stream when chatId changes", async () => {
    // First chat: history loads, then stream starts but doesn't complete.
    api.getChat.mockResolvedValueOnce({
      chat: { chat_id: "c1" },
      messages: [],
      older_cursor: null,
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
      older_cursor: null,
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
      older_cursor: null,
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
      older_cursor: null,
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
      older_cursor: null,
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
      older_cursor: null,
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

  it("preserves optimistic turns when history-load resolves after send()", async () => {
    // Covers the prev.length !== 0 branch of the history-merge setTurns:
    // first-message-creates-chat flow has send() push optimistic turns
    // BEFORE api.getChat resolves. The resolution must keep prev, not
    // overwrite with messages (which would wipe SSE-target rows).
    let resolveHistory;
    api.getChat.mockReturnValueOnce(
      new Promise((res) => {
        resolveHistory = res;
      }),
    );
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        { type: "user_persisted", msg_id: "user-x", seq: 0 },
        { type: "delta", text: "ok" },
        {
          type: "done",
          msg_id: "asst-x",
          seq: 1,
          model: "m",
          input_tokens: 0,
          output_tokens: 0,
          stop_reason: "end_turn",
        },
      ]),
    });

    const { result } = renderHook(() => useChatStream("c1"));
    // status starts at "loading-history" because chatId is set and the
    // getChat promise hasn't resolved.

    // Fire send WHILE history is still pending.
    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    // The optimistic + streamed turns are in place.
    expect(result.current.turns.map((t) => t.role)).toEqual([
      "user",
      "assistant",
    ]);

    // Now history finally resolves with some leftover history rows.
    // The merge should preserve the in-flight turns rather than
    // overwriting them.
    await act(async () => {
      resolveHistory({
        chat: { chat_id: "c1" },
        messages: [{ msg_id: "hist-1", role: "user", text: "old" }],
        older_cursor: null,
      });
    });

    expect(result.current.turns.map((t) => t.msg_id)).toEqual([
      "user-x",
      "asst-x",
    ]);
  });

  // ──────────────────────────────────────────────────────────────────
  // #181 PR-3: tool_started / tool_progress / tool_finished / tool_error
  // ──────────────────────────────────────────────────────────────────

  // Shared helpers for the tool-event suite.  Each test mocks history +
  // a stream of SSE events, then renders the hook, waits for idle, and
  // invokes `send()`.  Extracting the boilerplate keeps each test
  // focused on the unique events / assertions it exercises.

  const doneEvent = (msg_id) => ({
    type: "done",
    msg_id,
    seq: 1,
    model: "m",
    input_tokens: 0,
    output_tokens: 0,
    stop_reason: "end_turn",
  });

  const toolStarted = ({ id, name = "current_time", args = "{}" }) => ({
    type: "tool_started",
    tool_name: name,
    tool_use_id: id,
    args_preview: args,
  });

  async function runToolStream(events, { history = [] } = {}) {
    api.getChat.mockResolvedValue({
      chat: { chat_id: "c1" },
      messages: history,
      older_cursor: null,
    });
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody(events),
    });

    const { result } = renderHook(() => useChatStream("c1"));
    await waitFor(() => expect(result.current.status).toBe("idle"));

    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    return result;
  }

  it("attaches a tool step to the active assistant turn on tool_started", async () => {
    const result = await runToolStream([
      { type: "user_persisted", msg_id: "u-t1", seq: 0 },
      { type: "delta", text: "Let me check. " },
      toolStarted({ id: "tu-1" }),
      doneEvent("a-t1"),
    ]);

    const asst = result.current.turns.find((t) => t.msg_id === "a-t1");
    expect(asst.toolSteps).toHaveLength(1);
    expect(asst.toolSteps[0]).toMatchObject({
      toolUseId: "tu-1",
      toolName: "current_time",
      argsPreview: "{}",
      statusText: null,
      summary: null,
      errorType: null,
      partialResultCount: 0,
      status: "running",
    });
  });

  it("updates statusText on tool_progress without changing other fields", async () => {
    const result = await runToolStream([
      toolStarted({ id: "tu-2", name: "web_search", args: '{"q":"x"}' }),
      {
        type: "tool_progress",
        tool_use_id: "tu-2",
        status_text: "searching...",
      },
      doneEvent("a-t2"),
    ]);

    const asst = result.current.turns.find((t) => t.msg_id === "a-t2");
    expect(asst.toolSteps).toHaveLength(1);
    expect(asst.toolSteps[0]).toMatchObject({
      toolUseId: "tu-2",
      toolName: "web_search",
      argsPreview: '{"q":"x"}',
      statusText: "searching...",
      status: "running",
    });
  });

  it("marks the matching step as finished on tool_finished", async () => {
    const result = await runToolStream([
      toolStarted({ id: "tu-3" }),
      { type: "tool_finished", tool_use_id: "tu-3", summary: "completed" },
      doneEvent("a-t3"),
    ]);

    const asst = result.current.turns.find((t) => t.msg_id === "a-t3");
    expect(asst.toolSteps[0]).toMatchObject({
      toolUseId: "tu-3",
      summary: "completed",
      status: "finished",
    });
  });

  it("flips status to finished even when tool_finished arrives AFTER done (#221)", async () => {
    // #221 regression guard: the chassis sometimes flushes
    // tool_finished alongside or after the final ``done`` frame.
    // Without the client_msg_id stable lookup, the msg_id swap on
    // ``done`` makes patchToolStep's findIndex return -1 and the
    // step stays on ``"running"`` forever.
    const result = await runToolStream([
      toolStarted({ id: "tu-late" }),
      doneEvent("a-late"),
      // tool_finished arrives AFTER the done event that swapped msg_id:
      { type: "tool_finished", tool_use_id: "tu-late", summary: "completed" },
    ]);

    const asst = result.current.turns.find((t) => t.msg_id === "a-late");
    expect(asst.toolSteps[0]).toMatchObject({
      toolUseId: "tu-late",
      summary: "completed",
      status: "finished",
    });
  });

  it("routes kind + payload from tool_finished onto the step", async () => {
    const sandboxPayload = {
      stdout: "42\n",
      stderr: "",
      exit_code: 0,
      duration_ms: 12,
      truncated: false,
      timed_out: false,
      images: [],
    };
    const result = await runToolStream([
      toolStarted({ id: "tu-code", name: "code_exec", args: '{"code":"print(42)"}' }),
      {
        type: "tool_finished",
        tool_use_id: "tu-code",
        summary: "completed",
        kind: "code-output",
        payload: sandboxPayload,
      },
      doneEvent("a-code"),
    ]);

    const asst = result.current.turns.find((t) => t.msg_id === "a-code");
    expect(asst.toolSteps[0]).toMatchObject({
      toolUseId: "tu-code",
      kind: "code-output",
      payload: sandboxPayload,
      summary: "completed",
      status: "finished",
    });
  });

  it("marks the matching step as error on tool_error", async () => {
    const result = await runToolStream([
      toolStarted({ id: "tu-4", name: "web_search" }),
      {
        type: "tool_error",
        tool_use_id: "tu-4",
        error_type: "chain_cap",
        partial_result_count: 2,
      },
      doneEvent("a-t4"),
    ]);

    const asst = result.current.turns.find((t) => t.msg_id === "a-t4");
    expect(asst.toolSteps[0]).toMatchObject({
      toolUseId: "tu-4",
      errorType: "chain_cap",
      partialResultCount: 2,
      status: "error",
    });
  });

  it("is a no-op when tool_progress / tool_finished / tool_error reference an unknown tool_use_id", async () => {
    const result = await runToolStream([
      toolStarted({ id: "tu-known" }),
      // tool_use_id doesn't match anything in toolSteps — no-op.
      {
        type: "tool_progress",
        tool_use_id: "tu-unknown",
        status_text: "ignored",
      },
      { type: "tool_finished", tool_use_id: "tu-unknown", summary: "ignored" },
      {
        type: "tool_error",
        tool_use_id: "tu-unknown",
        error_type: "ignored",
        partial_result_count: 0,
      },
      doneEvent("a-t5"),
    ]);

    const asst = result.current.turns.find((t) => t.msg_id === "a-t5");
    expect(asst.toolSteps).toHaveLength(1);
    // The known step stays in its initial "running" state — none of
    // the unknown-id events patched it.
    expect(asst.toolSteps[0]).toMatchObject({
      toolUseId: "tu-known",
      status: "running",
      statusText: null,
      summary: null,
      errorType: null,
    });
  });

  it("is a no-op when tool_progress / tool_finished / tool_error arrive before any tool_started", async () => {
    // No tool_started ever fires, so the in-flight assistant turn never
    // gets a toolSteps array. patchToolStep's findIndex predicate
    // (`t.msg_id === tempAsstId && t.toolSteps`) returns -1 for the
    // turn-not-found branch. The events must NOT crash or fabricate a
    // toolSteps key on the turn.
    const result = await runToolStream([
      { type: "tool_progress", tool_use_id: "tu-orphan", status_text: "x" },
      { type: "tool_finished", tool_use_id: "tu-orphan", summary: "completed" },
      {
        type: "tool_error",
        tool_use_id: "tu-orphan",
        error_type: "ignored",
        partial_result_count: 0,
      },
      doneEvent("a-orphan"),
    ]);

    const asst = result.current.turns.find((t) => t.msg_id === "a-orphan");
    // No toolSteps key materialised on the turn — patchToolStep's
    // turn-not-found early return preserved it.
    expect(asst.toolSteps).toBeUndefined();
  });

  it("leaves unrelated history turns untouched when tool events arrive", async () => {
    // Seed an existing history row so patchToolStep's first false-branch
    // (t.msg_id !== tempAsstId) is exercised against a real turn.
    const result = await runToolStream(
      [
        toolStarted({ id: "tu-6" }),
        { type: "tool_progress", tool_use_id: "tu-6", status_text: "ticking" },
        doneEvent("a-t6"),
      ],
      { history: [{ msg_id: "hist-1", role: "user", text: "earlier" }] },
    );

    // History turn untouched (no toolSteps key sneaks onto it).
    const hist = result.current.turns.find((t) => t.msg_id === "hist-1");
    expect(hist).toMatchObject({ msg_id: "hist-1", role: "user" });
    expect(hist.toolSteps).toBeUndefined();
    // Active assistant turn picked up both events.
    const asst = result.current.turns.find((t) => t.msg_id === "a-t6");
    expect(asst.toolSteps).toHaveLength(1);
    expect(asst.toolSteps[0].statusText).toBe("ticking");
  });

  it("still attaches the step when tool_started arrives after done (#221)", async () => {
    // #221 fix: the assistant turn now carries a stable
    // ``client_msg_id`` that does NOT swap on ``done`` (msg_id swaps
    // to the persisted server id; client_msg_id stays put). A late
    // ``tool_started`` whose tempAsstId matches client_msg_id should
    // still create a step on the persisted row — without this fix
    // the chassis's occasional post-``done`` tool flushes would
    // disappear silently.
    const result = await runToolStream([
      { type: "delta", text: "ok" },
      doneEvent("a-late"),
      // tool_started arrives AFTER done — msg_id is now the persisted
      // id; client_msg_id is still tempAsstId, so the lookup finds it.
      toolStarted({ id: "tu-late" }),
    ]);

    const asst = result.current.turns.find((t) => t.msg_id === "a-late");
    expect(asst.toolSteps).toHaveLength(1);
    expect(asst.toolSteps[0]).toMatchObject({
      toolUseId: "tu-late",
      status: "running",
    });
  });

  it("defaults partialResultCount to 0 when tool_error omits partial_result_count", async () => {
    // Older / partial backends may emit tool_error without
    // partial_result_count. Copilot iteration: defensive `?? 0` keeps
    // the chain-cap copy stable (`{N} steps completed`) rather than
    // rendering `undefined` into the UI.
    const result = await runToolStream([
      toolStarted({ id: "tu-no-count" }),
      {
        type: "tool_error",
        tool_use_id: "tu-no-count",
        error_type: "chain_cap",
        // partial_result_count intentionally omitted
      },
      doneEvent("a-t-no-count"),
    ]);

    const asst = result.current.turns.find((t) => t.msg_id === "a-t-no-count");
    expect(asst.toolSteps[0]).toMatchObject({
      toolUseId: "tu-no-count",
      errorType: "chain_cap",
      partialResultCount: 0,
      status: "error",
    });
  });

  it("clears stale turns when switching chats (loadedChatIdRef branch)", async () => {
    // Covers the loadedChatIdRef.current !== chatId branch: switching
    // from one chat with turns to a different chat clears the old
    // turns before the new history loads.
    api.getChat
      .mockResolvedValueOnce({
        chat: { chat_id: "c1" },
        messages: [{ msg_id: "old", role: "user", text: "old" }],
        older_cursor: null,
      })
      .mockResolvedValueOnce({
        chat: { chat_id: "c2" },
        messages: [{ msg_id: "new", role: "user", text: "new" }],
        older_cursor: null,
      });

    const { result, rerender } = renderHook(({ id }) => useChatStream(id), {
      initialProps: { id: "c1" },
    });
    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.turns[0].msg_id).toBe("old");

    rerender({ id: "c2" });
    await waitFor(() => expect(result.current.status).toBe("idle"));
    expect(result.current.turns[0].msg_id).toBe("new");
  });

  it("exposes hasOlder=false when the initial load returns no older_cursor", async () => {
    api.getChat.mockResolvedValue(
      chatPage([{ msg_id: "m1", role: "user", text: "hello" }]),
    );

    const { result } = await mountLoaded();
    expect(result.current.hasOlder).toBe(false);
  });

  it("loadOlder prepends the previous page and advances the cursor (#270)", async () => {
    // Initial load: newest page with an older_cursor → hasOlder true.
    api.getChat.mockResolvedValueOnce(
      chatPage([{ msg_id: "m3", role: "user", text: "newest" }], "cur-1"),
    );

    const { result } = await mountLoaded();
    expect(result.current.hasOlder).toBe(true);
    expect(result.current.turns.map((t) => t.msg_id)).toEqual(["m3"]);

    // Older page: prepended above the current head; no further cursor.
    api.getChat.mockResolvedValueOnce(
      chatPage([
        { msg_id: "m1", role: "user", text: "oldest" },
        { msg_id: "m2", role: "assistant", text: "older" },
      ]),
    );

    await act(async () => {
      await result.current.loadOlder();
    });

    expect(api.getChat).toHaveBeenLastCalledWith("c1", { before: "cur-1" });
    expect(result.current.turns.map((t) => t.msg_id)).toEqual(["m1", "m2", "m3"]);
    expect(result.current.hasOlder).toBe(false);
  });

  it("loadOlder for a previous chat does not corrupt the new chat after switching (#270)", async () => {
    // c1 initial load → an older page is available.
    api.getChat.mockResolvedValueOnce(
      chatPage([{ msg_id: "c1-newest", role: "user", text: "a" }], "cur-1"),
    );
    const { result, rerender } = await mountLoaded("c1");

    // Start loadOlder for c1 but hold the fetch open with a deferred promise.
    let resolveOlder;
    api.getChat.mockReturnValueOnce(
      new Promise((res) => {
        resolveOlder = res;
      }),
    );
    let pending;
    act(() => {
      pending = result.current.loadOlder();
    });

    // Switch to c2 while c1's older-page fetch is still in flight.
    api.getChat.mockResolvedValueOnce(
      chatPage([{ msg_id: "c2-only", role: "user", text: "b" }], null, "c2"),
    );
    rerender({ id: "c2" });
    await waitFor(() =>
      expect(result.current.turns.map((t) => t.msg_id)).toEqual(["c2-only"]),
    );

    // The stale c1 older-page now resolves — it must NOT touch c2's turns.
    await act(async () => {
      resolveOlder(chatPage([{ msg_id: "c1-older", role: "user", text: "stale" }]));
      await pending;
    });

    expect(result.current.turns.map((t) => t.msg_id)).toEqual(["c2-only"]);
  });

  it("loadOlder swallows fetch errors so a fire-and-forget call can't reject (#270)", async () => {
    api.getChat.mockResolvedValueOnce(
      chatPage([{ msg_id: "m1", role: "user", text: "a" }], "cur-1"),
    );
    const { result } = await mountLoaded();

    api.getChat.mockRejectedValueOnce(new Error("network"));
    await act(async () => {
      await expect(result.current.loadOlder()).resolves.toBeUndefined();
    });

    // Current turns stay usable and the cursor is unchanged (retryable).
    expect(result.current.turns.map((t) => t.msg_id)).toEqual(["m1"]);
    expect(result.current.hasOlder).toBe(true);
  });

  it("loadOlder is a no-op when there is no older page", async () => {
    api.getChat.mockResolvedValue(
      chatPage([{ msg_id: "m1", role: "user", text: "hello" }]),
    );

    const { result } = await mountLoaded();
    api.getChat.mockClear();

    await act(async () => {
      await result.current.loadOlder();
    });

    expect(api.getChat).not.toHaveBeenCalled();
  });

  // --- stream-failure surfacing (#212) ---

  // Like makeMockResponseBody, but the stream errors (instead of
  // closing) after the queued events drain — simulates a network drop
  // or an intentional AbortController.abort() mid-stream.
  function makeFailingBody(events, err) {
    const encoder = new TextEncoder();
    const chunks = events.map((e) => `data: ${JSON.stringify(e)}\n\n`);
    let i = 0;
    return new ReadableStream({
      pull(controller) {
        if (i >= chunks.length) {
          controller.error(err);
          return;
        }
        controller.enqueue(encoder.encode(chunks[i]));
        i += 1;
      },
    });
  }

  it("marks the turn errored and idles on an `error` frame, keeping partial text", async () => {
    api.getChat.mockResolvedValue(chatPage([]));
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        { type: "user_persisted", msg_id: "user-1", seq: 0 },
        { type: "delta", text: "par" },
        {
          type: "error",
          code: "bedrock_throttled",
          message: "Busy.",
          retryable: true,
        },
        {
          type: "done",
          msg_id: "",
          seq: 1,
          model: "m",
          input_tokens: 0,
          output_tokens: 0,
          stop_reason: "error",
        },
      ]),
    });

    const { result } = await mountLoaded();
    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    const asst = result.current.turns[1];
    expect(asst.streaming).toBe(false);
    expect(asst.streamError).toEqual({
      code: "bedrock_throttled",
      message: "Busy.",
      retryable: true,
    });
    expect(asst.text).toBe("par");
    // The error-path `done` carries msg_id: "" (nothing persisted) —
    // the temp id must survive so the turn keeps a usable React key.
    expect(asst.msg_id).toMatch(/^tmp-a-/);
    expect(result.current.status).toBe("idle");
  });

  it("treats a clean EOF without a terminal frame as connection_lost", async () => {
    api.getChat.mockResolvedValue(chatPage([]));
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        { type: "user_persisted", msg_id: "user-1", seq: 0 },
        { type: "delta", text: "Hel" },
      ]),
    });

    const { result } = await mountLoaded();
    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    const asst = result.current.turns[1];
    expect(asst.streaming).toBe(false);
    expect(asst.streamError).toMatchObject({
      code: "connection_lost",
      retryable: true,
    });
    expect(result.current.status).toBe("idle");
  });

  it("treats a mid-stream reader failure as connection_lost", async () => {
    api.getChat.mockResolvedValue(chatPage([]));
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeFailingBody(
        [{ type: "delta", text: "x" }],
        new Error("network reset"),
      ),
    });

    const { result } = await mountLoaded();
    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    const asst = result.current.turns[1];
    expect(asst.streaming).toBe(false);
    expect(asst.streamError).toMatchObject({ code: "connection_lost" });
    expect(result.current.status).toBe("idle");
  });

  it("stays silent when the reader rejects with AbortError (user-initiated)", async () => {
    const abortErr = Object.assign(new Error("aborted"), {
      name: "AbortError",
    });
    api.getChat.mockResolvedValue(chatPage([]));
    api.streamMessage.mockResolvedValue({
      ok: true,
      body: makeFailingBody([{ type: "delta", text: "x" }], abortErr),
    });

    const { result } = await mountLoaded();
    await act(async () => {
      await result.current.send({ message: "hi", model: "m", effort: "med" });
    });

    const asst = result.current.turns[1];
    expect(asst.streamError).toBeUndefined();
    expect(asst.streaming).toBe(true);
    expect(result.current.status).toBe("streaming");
  });

  it("marks the regenerated turn errored on an `error` frame", async () => {
    api.getChat.mockResolvedValue(
      chatPage([
        { msg_id: "u1", role: "user", text: "q" },
        { msg_id: "a1", role: "assistant", text: "old" },
      ]),
    );
    api.regenerate.mockResolvedValue({
      ok: true,
      body: makeMockResponseBody([
        {
          type: "error",
          code: "internal",
          message: "Something went wrong.",
          retryable: true,
        },
        {
          type: "done",
          msg_id: "",
          seq: 1,
          model: "m",
          input_tokens: 0,
          output_tokens: 0,
          stop_reason: "error",
        },
      ]),
    });

    const { result } = await mountLoaded();
    await act(async () => {
      await result.current.regenerate({});
    });

    const last = result.current.turns[result.current.turns.length - 1];
    expect(last.role).toBe("assistant");
    expect(last.streaming).toBe(false);
    expect(last.streamError).toMatchObject({ code: "internal" });
    expect(result.current.status).toBe("idle");
  });
});
