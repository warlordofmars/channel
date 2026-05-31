// Copyright (c) 2026 John Carter. All rights reserved.

/**
 * Parse zero or more complete SSE events from a string. Lines beginning
 * with `:` are SSE comments (keepalives) and are ignored. `data:` lines
 * are JSON-parsed; malformed payloads are dropped silently rather than
 * crashing the stream.
 *
 * @param {string} chunk - one or more complete `data: ...\n\n` events
 * @returns {Array<object>} parsed event objects
 */
export function parseSseChunk(chunk) {
  const events = [];
  const blocks = chunk.split("\n\n");
  for (const block of blocks) {
    if (!block) continue;
    let payload = null;
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("data:")) {
        payload = line.slice(5).trimStart();
      }
    }
    if (payload == null) continue;
    try {
      events.push(JSON.parse(payload));
    } catch {
      // malformed payload — drop and continue
    }
  }
  return events;
}

/**
 * Stateful SSE decoder for use with Web Streams. Buffers partial events
 * across chunks; `feed(chunk)` returns the complete events that became
 * available with this chunk.
 *
 * @returns {{feed: (chunk: string) => Array<object>}}
 */
export function makeSseDecoder() {
  let buffer = "";
  return {
    feed(chunk) {
      buffer += chunk;
      const separatorIndex = buffer.lastIndexOf("\n\n");
      if (separatorIndex === -1) return [];
      const complete = buffer.slice(0, separatorIndex + 2);
      buffer = buffer.slice(separatorIndex + 2);
      return parseSseChunk(complete);
    },
  };
}
