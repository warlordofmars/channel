// Copyright (c) 2026 John Carter. All rights reserved.
import { useEffect, useState } from "react";
import { getAssetContent } from "../api.js";

/**
 * Fetch a chat asset's Bearer-authenticated payload and expose it to a
 * renderer. Asset content is API-mediated (no presigned URLs), so a
 * plain `<img src={content-url}>` can't carry the auth header — the bytes
 * must be fetched, wrapped in a blob, and handed to the element as an
 * object URL. This hook is the single home for that pattern, extracted
 * from `ArtifactPanel` (#328) so both the transcript's inline image
 * render (#361) and the forthcoming inline data/document previews (#363)
 * share one fetch + object-URL-lifecycle implementation.
 *
 * Two read modes:
 *   - `"blob"` (default) — reads `response.blob()` and exposes an
 *     `URL.createObjectURL` object URL as `url`. The URL is revoked when
 *     the hook unmounts or its inputs change, so a scrolled-away image
 *     never leaks its blob.
 *   - `"text"` — reads `response.text()` and exposes it as `text`.
 *
 * Returns `{ status, url, text, httpStatus }`:
 *   - `status: "idle"`    — not fetching. Either `enabled` is false, or a
 *                           required id is missing. Callers treat this as
 *                           "no content" (e.g. show the fallback card).
 *   - `status: "loading"` — a fetch is in flight.
 *   - `status: "ready"`   — payload available: `url` (blob mode) or
 *                           `text` (text mode).
 *   - `status: "error"`   — the fetch rejected. `httpStatus` carries the
 *                           HTTP status when known (404 for a gone S3
 *                           object, else the failure status or null) so
 *                           callers can distinguish gone-vs-transient.
 *
 * `enabled` lets a caller mount the hook unconditionally (Rules of Hooks)
 * while suppressing the fetch for kinds that render without bytes — e.g.
 * ArtifactPanel's diagram/unknown download-only fallback.
 */
export function useAssetContent(chatId, assetId, { mode = "blob", enabled = true } = {}) {
  const [result, setResult] = useState({
    status: "idle",
    url: null,
    text: null,
    httpStatus: null,
  });

  useEffect(function loadAssetContent() {
    // Guard the fetch: a disabled hook or a malformed descriptor (missing
    // either id) must never issue `/api/chats/null/...`. Reset to idle so
    // stale content from a previous asset can't linger after a swap.
    if (!enabled || !chatId || !assetId) {
      setResult({ status: "idle", url: null, text: null, httpStatus: null });
      return undefined;
    }
    let cancelled = false;
    let objectUrl = null;
    setResult({ status: "loading", url: null, text: null, httpStatus: null });
    getAssetContent(chatId, assetId)
      .then(async function onAssetResponse(response) {
        if (mode === "blob") {
          const blob = await response.blob();
          objectUrl = URL.createObjectURL(blob);
          if (cancelled) {
            // Resolved after unmount / asset change — drop the blob we
            // just created so it can't leak.
            URL.revokeObjectURL(objectUrl);
            return;
          }
          setResult({ status: "ready", url: objectUrl, text: null, httpStatus: null });
        } else {
          const text = await response.text();
          if (!cancelled) {
            setResult({ status: "ready", url: null, text, httpStatus: null });
          }
        }
      })
      .catch(function onAssetError(err) {
        if (!cancelled) {
          setResult({ status: "error", url: null, text: null, httpStatus: err?.status ?? null });
        }
      });
    return function cleanupAssetContent() {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [chatId, assetId, mode, enabled]);

  return result;
}
