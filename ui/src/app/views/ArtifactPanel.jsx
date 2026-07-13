// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef, useState } from "react";
import Icon from "../../components/Icon.jsx";
import { getAssetContent } from "../../api.js";
import { renderMarkdown } from "../renderMarkdown.jsx";
import { artIcon, formatBytes, kindLabel } from "./artifactHelpers.js";

const COPIED_FEEDBACK_MS = 1500;

// Kinds whose payload is UTF-8 text we render inline. `image` streams a
// blob → object URL; everything else (`diagram` and any future kind) is
// download-only.
const TEXT_KINDS = new Set(["code", "document", "data"]);

/**
 * Copy text to the clipboard via the async Clipboard API. Returns a
 * Promise resolving to whether the write succeeded. The deployed app and
 * `localhost` are both secure contexts, so the legacy `execCommand`
 * fallback isn't needed here (unlike the transcript's CopyButton, which
 * can render in a non-secure jsdom probe). Errors are swallowed — a
 * clipboard failure is not worth a console error.
 */
export async function copyAssetText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/**
 * Parse a CSV payload into a row-of-cells matrix for the `data` renderer.
 * Deliberately minimal (split on newlines, then commas) — v1 data assets
 * are producer-generated CSV, not arbitrary quoted-field spreadsheets.
 * Blank lines are dropped so a trailing newline doesn't add an empty row.
 */
export function parseCsv(text) {
  return text
    .trim()
    .split(/\r?\n/)
    .filter((line) => line.length > 0)
    .map((line) => line.split(","));
}

/**
 * Trigger a browser download of `blob` named `filename` via a transient
 * anchor. Extracted + named so the click path is a single covered unit.
 */
export function triggerBlobDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename || "artifact";
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

/**
 * Slide-in asset viewer panel (#328). Fetches the asset payload from the
 * per-chat content route and renders it by `kind`:
 *
 *   - `code`     — `<pre><code>` from the text payload
 *   - `document` — markdown via `renderMarkdown`
 *   - `data`     — CSV → table
 *   - `image`    — Bearer-header fetch → blob URL → `<img>`
 *   - fallback   — download-only affordance (`diagram` + unknown kinds)
 *
 * `artifact` is the REST card descriptor
 * (`{asset_id, chat_id, kind, title, mime, size_bytes, ...}`) — the same
 * shape `GET /api/assets` and the `asset_created` SSE frame emit, so the
 * transcript's inline cards (#327) can mount this panel with an
 * unchanged prop contract. When `artifact` is null the component renders
 * nothing, so callers can mount it unconditionally and flip the prop.
 *
 * Copy (text kinds) and Download (all kinds) are real: Copy writes the
 * fetched text to the clipboard; Download streams a fresh blob and saves
 * it. Content-fetch failures render a graceful state — 404 (the S3 object
 * is gone) and 502 (other S3 failure) each get distinct copy.
 */
export default function ArtifactPanel({ artifact, onClose }) {
  const asset = artifact;
  const assetId = asset ? asset.asset_id : null;
  const chatId = asset ? asset.chat_id : null;
  const kind = asset ? asset.kind : null;

  const [content, setContent] = useState({ state: "idle" });
  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef(null);

  useEffect(function clearCopyTimerOnUnmount() {
    return function cleanup() {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  useEffect(function fetchAssetContent() {
    if (!assetId) return undefined;
    // Reset the copy affordance whenever the open asset changes.
    setCopied(false);
    if (!TEXT_KINDS.has(kind) && kind !== "image") {
      setContent({ state: "fallback" });
      return undefined;
    }
    let cancelled = false;
    let objectUrl = null;
    setContent({ state: "loading" });
    getAssetContent(chatId, assetId)
      .then(async function onResponse(response) {
        if (kind === "image") {
          const blob = await response.blob();
          objectUrl = URL.createObjectURL(blob);
          if (cancelled) {
            URL.revokeObjectURL(objectUrl);
            return;
          }
          setContent({ state: "image", url: objectUrl });
        } else {
          const text = await response.text();
          if (!cancelled) setContent({ state: "text", text });
        }
      })
      .catch(function onError(err) {
        if (!cancelled) setContent({ state: "error", status: err?.status ?? null });
      });
    return function cleanup() {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [assetId, chatId, kind]);

  async function handleCopy() {
    // Only ever bound to the Copy button, which renders only for text
    // content — so `content.text` is always present here.
    const ok = await copyAssetText(content.text);
    if (!ok) return;
    setCopied(true);
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    copyTimerRef.current = setTimeout(function clearCopied() {
      setCopied(false);
    }, COPIED_FEEDBACK_MS);
  }

  async function handleDownload() {
    try {
      const response = await getAssetContent(chatId, assetId);
      const blob = await response.blob();
      triggerBlobDownload(blob, asset.title);
    } catch {
      /* best-effort — a failed download surfaces nothing to the user */
    }
  }

  if (!asset) return null;

  const canCopy = content.state === "text";
  const sizeLabel = formatBytes(asset.size_bytes);
  const sub = sizeLabel ? `${kindLabel(kind)} · ${sizeLabel}` : kindLabel(kind);

  return (
    <div className="art-panel-wrap">
      <div className="art-backdrop" onClick={onClose} />
      <div className="art-panel">
        <div className="art-phead">
          <span className="ic"><Icon name={artIcon(kind)} size={18} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="t">{asset.title}</div>
            <div className="s mono">{sub}</div>
          </div>
          {canCopy && (
            <button
              type="button"
              className="icon-btn"
              title={copied ? "Copied" : "Copy"}
              aria-label={copied ? "Copied" : "Copy"}
              onClick={handleCopy}
            >
              <Icon name={copied ? "check" : "copy"} size={17} />
            </button>
          )}
          <button
            type="button"
            className="icon-btn"
            title="Download"
            aria-label="Download"
            onClick={handleDownload}
          >
            <Icon name="download" size={17} />
          </button>
          <button
            type="button"
            className="icon-btn"
            title="Close"
            aria-label="Close"
            onClick={onClose}
          >
            <Icon name="close" size={18} />
          </button>
        </div>
        <div className="art-pbody">
          <ArtifactBody
            kind={kind}
            title={asset.title}
            content={content}
            onDownload={handleDownload}
          />
        </div>
      </div>
    </div>
  );
}

/** The kind- and fetch-state-driven body of the open panel. */
function ArtifactBody({ kind, title, content, onDownload }) {
  if (content.state === "loading") {
    return <div className="art-loading">Loading…</div>;
  }
  if (content.state === "error") {
    const message =
      content.status === 404
        ? "This artifact's content is no longer available."
        : "Couldn't load this artifact right now. Try again in a moment.";
    return (
      <div className="art-error" role="alert">
        {message}
      </div>
    );
  }
  if (content.state === "image") {
    return (
      <div className="art-image">
        <img src={content.url} alt={title} />
      </div>
    );
  }
  if (content.state === "text") {
    if (kind === "code") {
      return <pre className="art-code"><code>{content.text}</code></pre>;
    }
    if (kind === "data") {
      return <CsvTable text={content.text} />;
    }
    return <div className="art-doc">{renderMarkdown(content.text, false)}</div>;
  }
  // fallback / idle — download-only affordance for diagram + unknown kinds.
  return (
    <div className="art-fallback">
      <Icon name="download" size={22} />
      <div>Preview isn&apos;t available for this artifact.</div>
      <button type="button" className="art-loadmore" onClick={onDownload}>
        Download
      </button>
    </div>
  );
}

/** CSV text → a `.art-table`; first row is the header. */
function CsvTable({ text }) {
  const rows = parseCsv(text);
  if (rows.length === 0) {
    return <div className="art-error">This data artifact is empty.</div>;
  }
  const [header, ...body] = rows;
  return (
    <table className="art-table">
      <thead>
        <tr>
          {header.map((cell, i) => (
            <th key={i}>{cell}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {body.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => (
              <td key={j} className={j > 0 ? "mono" : ""}>{cell}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
