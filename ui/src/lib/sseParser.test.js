// Copyright (c) 2026 John Carter. All rights reserved.
import { describe, it, expect } from "vitest";
import { parseSseChunk, makeSseDecoder } from "./sseParser.js";

describe("parseSseChunk", () => {
  it("parses a single complete event", () => {
    const events = parseSseChunk('data: {"type":"delta","text":"hi"}\n\n');
    expect(events).toEqual([{ type: "delta", text: "hi" }]);
  });

  it("parses multiple events in one chunk", () => {
    const raw =
      'data: {"type":"delta","text":"a"}\n\n' +
      'data: {"type":"delta","text":"b"}\n\n';
    const events = parseSseChunk(raw);
    expect(events).toEqual([
      { type: "delta", text: "a" },
      { type: "delta", text: "b" },
    ]);
  });

  it("ignores comment lines starting with colon", () => {
    const events = parseSseChunk(':keepalive\n\ndata: {"type":"done"}\n\n');
    expect(events).toEqual([{ type: "done" }]);
  });

  it("skips malformed JSON gracefully", () => {
    const events = parseSseChunk("data: not-json\n\n");
    expect(events).toEqual([]);
  });

  it("ignores non-data, non-comment lines (e.g. event:)", () => {
    const events = parseSseChunk('event: delta\ndata: {"type":"delta","text":"y"}\n\n');
    expect(events).toEqual([{ type: "delta", text: "y" }]);
  });
});

describe("makeSseDecoder", () => {
  it("buffers a split event across two chunks", () => {
    const decoder = makeSseDecoder();
    const first = decoder.feed('data: {"type":"delta",');
    expect(first).toEqual([]);
    const second = decoder.feed('"text":"hi"}\n\n');
    expect(second).toEqual([{ type: "delta", text: "hi" }]);
  });

  it("handles a trailing partial event without losing data", () => {
    const decoder = makeSseDecoder();
    decoder.feed('data: {"type":"done"}\n\ndata: {"typ');
    expect(decoder.feed('e":"delta","text":"x"}\n\n')).toEqual([
      { type: "delta", text: "x" },
    ]);
  });
});
