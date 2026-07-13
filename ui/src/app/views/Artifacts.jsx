// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import Icon from "../../components/Icon.jsx";
import ArtifactPanel from "./ArtifactPanel.jsx";
import { getAsset, listAssets } from "../../api.js";
import { artIcon, formatBytes, kindLabel, relativeTime } from "./artifactHelpers.js";

const ARTIFACT_PARAM = "artifact";
const CHAT_PARAM = "chat";
const PAGE_LIMIT = 30;

/**
 * Drain browse pages starting at `fromCursor` until at least one item is
 * collected or the cursor is exhausted (`next_cursor === null`).
 *
 * The `GET /api/assets` contract (#325) can return a short — even empty —
 * page alongside a live `next_cursor` because the server drops orphaned
 * rows mid-page and lazily reaps them. Paging on `items.length` would
 * stall on the first sparse page; the only end-of-list signal is a null
 * cursor. Draining leading empty pages here keeps "Load more" meaningful
 * (a click always makes progress) without violating the count rule.
 */
export async function drainAssetPages(fromCursor) {
  let collected = [];
  let cursor = fromCursor;
  for (;;) {
    const page = await listAssets({ limit: PAGE_LIMIT, cursor });
    collected = collected.concat(page.items);
    cursor = page.next_cursor ?? null;
    if (collected.length > 0 || cursor === null) break;
  }
  return { items: collected, nextCursor: cursor };
}

/**
 * `/app/artifacts` — the real cross-chat asset browse view (#328). Rows
 * come from `GET /api/assets` (newest-first, cursor-paginated) rather
 * than the retired `ARTIFACTS` mock. Each row shows the kind glyph,
 * title, and a meta line (kind · size · relative time).
 *
 * The detail panel is bookmarkable via `?artifact=<asset_id>&chat=<chat_id>`
 * — both ids are needed because the content route is per-chat. On a cold
 * deep-link (refresh/bookmark) where the asset isn't on the loaded page,
 * the descriptor is fetched directly rather than searched for.
 */
export default function Artifacts() {
  const [params, setParams] = useSearchParams();
  const [assets, setAssets] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [exhausted, setExhausted] = useState(false);
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [loadingMore, setLoadingMore] = useState(false);
  const [deepLinked, setDeepLinked] = useState(null);

  // Tracks whether the view is still mounted so the `loadMore` promise
  // handlers (which live outside an effect and so have no cleanup) don't
  // set state after the user navigates away mid-fetch.
  const mountedRef = useRef(true);
  useEffect(function trackMounted() {
    mountedRef.current = true;
    return function markUnmounted() {
      mountedRef.current = false;
    };
  }, []);

  const openId = params.get(ARTIFACT_PARAM);
  const openChat = params.get(CHAT_PARAM);
  const loadedOpen = openId ? assets.find((a) => a.asset_id === openId) ?? null : null;

  useEffect(function loadFirstPage() {
    let cancelled = false;
    setStatus("loading");
    drainAssetPages(null)
      .then(function onFirstPage({ items, nextCursor }) {
        if (cancelled) return;
        setAssets(items);
        setCursor(nextCursor);
        setExhausted(nextCursor === null);
        setStatus("ready");
      })
      .catch(function onFirstPageError() {
        if (!cancelled) setStatus("error");
      });
    return function cleanup() {
      cancelled = true;
    };
  }, []);

  // Cold deep-link: the URL points at an asset that isn't on the loaded
  // page, so fetch its descriptor directly (needs both ids). Gated until
  // the first page has settled so a link to a page-1 asset resolves from
  // the loaded card instead of a redundant round-trip. No-op when the
  // card is already loaded or the params are absent/partial.
  useEffect(function resolveDeepLink() {
    if (!openId || !openChat || loadedOpen || status === "loading") {
      setDeepLinked(null);
      return undefined;
    }
    let cancelled = false;
    // Clear any prior descriptor before the new fetch so the panel doesn't
    // flash the previously deep-linked asset while this one loads.
    setDeepLinked(null);
    getAsset(openChat, openId)
      .then(function onDescriptor(descriptor) {
        if (!cancelled) setDeepLinked(descriptor);
      })
      .catch(function onDescriptorError() {
        if (!cancelled) setDeepLinked(null);
      });
    return function cleanup() {
      cancelled = true;
    };
  }, [openId, openChat, loadedOpen, status]);

  const openAsset = loadedOpen ?? deepLinked;

  function openRow(a) {
    const next = new URLSearchParams(params);
    next.set(ARTIFACT_PARAM, a.asset_id);
    next.set(CHAT_PARAM, a.chat_id);
    setParams(next);
  }

  function closePanel() {
    const next = new URLSearchParams(params);
    next.delete(ARTIFACT_PARAM);
    next.delete(CHAT_PARAM);
    setParams(next);
  }

  function handleRowKey(event, a) {
    // Keyboard parity for the role="button" rows (Enter / Space open the
    // panel, same as a pointer click).
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openRow(a);
    }
  }

  function loadMore() {
    // The button only renders while `!exhausted` (so `cursor` is live) and
    // is `disabled` while a page is in flight, so re-entrancy can't happen.
    setLoadingMore(true);
    drainAssetPages(cursor)
      .then(function onMore({ items, nextCursor }) {
        if (!mountedRef.current) return;
        setAssets(function append(prev) {
          return prev.concat(items);
        });
        setCursor(nextCursor);
        setExhausted(nextCursor === null);
      })
      .catch(function onMoreError() {
        /* transient — keep the list + the button so the user can retry */
      })
      .finally(function settle() {
        if (mountedRef.current) setLoadingMore(false);
      });
  }

  return (
    <div className="view">
      <div className="view-inner">
        <div className="view-head">
          <h2>Artifacts</h2>
          <p>Documents, code, data, and images Channel has built with you.</p>
        </div>
        {status === "loading" && <div className="art-empty">Loading artifacts…</div>}
        {status === "error" && (
          <div className="art-empty" role="alert">
            Couldn&apos;t load your artifacts. Reload to try again.
          </div>
        )}
        {status === "ready" && assets.length === 0 && (
          <div className="art-empty">
            No artifacts yet. Code, documents, data, and images from your chats show up here.
          </div>
        )}
        {assets.map((a) => (
          <div
            className="list-row"
            key={a.asset_id}
            role="button"
            tabIndex={0}
            onClick={() => openRow(a)}
            onKeyDown={(e) => handleRowKey(e, a)}
          >
            <span className="badge"><Icon name={artIcon(a.kind)} size={18} /></span>
            <div className="info">
              <div className="ti">{a.title}</div>
              <div className="sub">
                {[kindLabel(a.kind), formatBytes(a.size_bytes), relativeTime(a.created_at)]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            </div>
            <span className="kind">{a.kind}</span>
          </div>
        ))}
        {status === "ready" && !exhausted && (
          <button
            type="button"
            className="art-loadmore"
            onClick={loadMore}
            disabled={loadingMore}
          >
            {loadingMore ? "Loading…" : "Load more"}
          </button>
        )}
      </div>
      <ArtifactPanel artifact={openAsset} onClose={closePanel} />
    </div>
  );
}
