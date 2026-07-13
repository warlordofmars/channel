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
 * Locate an asset's card descriptor by id across the owner's browse
 * pages, returning it (or null if not found). Used for the chat-less
 * `?artifact=<id>` deep link the inline transcript cards (#327) emit —
 * they carry no chat id, and the per-chat descriptor route needs one, so
 * the owner GSI browse is the only id→card path. Browse is newest-first,
 * so a just-created inline-card asset resolves on the first page; older
 * bookmarks page back until found or the cursor exhausts.
 *
 * `fromCursor` lets the caller resume from the browse cursor it already
 * holds (the tail past its loaded pages), skipping a redundant refetch of
 * pages the caller has already scanned; it defaults to the start.
 */
export async function findAssetById(assetId, fromCursor = null) {
  let cursor = fromCursor;
  for (;;) {
    const page = await listAssets({ limit: PAGE_LIMIT, cursor });
    const found = (page.items ?? []).find((a) => a.asset_id === assetId);
    if (found) return found;
    const next = page.next_cursor ?? null;
    // Stop on exhaustion or a non-advancing cursor — a server that returned
    // the same cursor would otherwise spin the loop forever.
    if (next === null || next === cursor) return null;
    cursor = next;
  }
}

/**
 * `/app/artifacts` — the real cross-chat asset browse view (#328). Rows
 * come from `GET /api/assets` (newest-first, cursor-paginated) rather
 * than the retired `ARTIFACTS` mock. Each row shows the kind glyph,
 * title, and a meta line (kind · size · relative time).
 *
 * The detail panel is bookmarkable via `?artifact=<asset_id>` (optionally
 * `&chat=<chat_id>`). Row clicks add the chat id so the descriptor is one
 * point read; the inline transcript cards (#327) link only the asset id,
 * which resolves via the owner browse. On a cold deep-link where the
 * asset isn't on the loaded page, the descriptor is fetched via the REST
 * surface rather than searched for in memory.
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

  // Latest browse state (the tail cursor past the loaded pages + whether
  // the browse is fully drained), read by the chat-less deep-link resolver
  // so it resumes the scan from the loaded tail — or skips it entirely when
  // the browse is exhausted and the asset still wasn't found. Held in a ref
  // so pagination changes don't re-run the deep-link effect (which would
  // flash the panel closed).
  const browseStateRef = useRef({ cursor: null, exhausted: false });
  useEffect(function trackBrowseState() {
    browseStateRef.current = { cursor, exhausted };
  }, [cursor, exhausted]);

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
  // page, so fetch its descriptor via the REST surface. Gated until the
  // first page has settled so a link to a page-1 asset resolves from the
  // loaded card instead of a redundant round-trip. Two URL shapes:
  //   - `?artifact=<id>&chat=<chat>` (a row click / full bookmark) → the
  //     per-chat descriptor route, one point read.
  //   - `?artifact=<id>` alone (an inline transcript card, #327, which
  //     carries no chat id) → locate the card across the owner's browse
  //     pages.
  // No-op when the card is already loaded or `artifact` is absent.
  useEffect(function resolveDeepLink() {
    if (!openId || loadedOpen || status === "loading") {
      setDeepLinked(null);
      return undefined;
    }
    let cancelled = false;
    // Clear any prior descriptor before the new fetch so the panel doesn't
    // flash the previously deep-linked asset while this one loads.
    setDeepLinked(null);
    const { cursor: browseCursor, exhausted: browseExhausted } = browseStateRef.current;
    // A fully-drained browse that didn't surface the asset (loadedOpen is
    // false) means it doesn't exist — skip the redundant page-1 refetch.
    const lookup = openChat
      ? getAsset(openChat, openId)
      : browseExhausted
        ? Promise.resolve(null)
        : findAssetById(openId, browseCursor);
    lookup
      .then(function onDescriptor(descriptor) {
        if (!cancelled) setDeepLinked(descriptor ?? null);
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
    // Ignore auto-repeat while a key is held so we don't spam the history
    // stack with identical ?artifact= states.
    if (event.repeat) return;
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
            <span className="kind">{kindLabel(a.kind)}</span>
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
