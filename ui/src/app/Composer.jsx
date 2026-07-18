// Copyright (c) 2026 John Carter. All rights reserved.
import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  finalizeAttachment,
  presignAttachment,
  sha256Hex,
  uploadToPresigned,
} from "../api.js";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
import AttachMenu, { ALLOWED_MIMES } from "./AttachMenu.jsx";
import MCPPicker from "./MCPPicker.jsx";
import ModelPicker from "./ModelPicker.jsx";

const ALLOWED_MIME_SET = new Set(ALLOWED_MIMES);
const MAX_SIZE_BYTES = 20 * 1024 * 1024;
const MAX_FILES_PER_MESSAGE = 5;

/**
 * Feature-detect the Web Speech API. Browsers that ship it expose
 * either the standardised `SpeechRecognition` constructor or the
 * vendor-prefixed `webkitSpeechRecognition` (Chrome, Edge, Safari).
 * Returns `null` on Firefox + everywhere the API isn't available.
 */
function getSpeechRecognition() {
  return window.SpeechRecognition ?? window.webkitSpeechRecognition ?? null;
}

let _tempIdCounter = 0;
function nextTempId() {
  _tempIdCounter += 1;
  return `pending-${_tempIdCounter}`;
}

// #211: a rejected onSend promise (e.g. ChatHome's createChat failing)
// is, from the Composer's seat, the same outcome as a refused send —
// the message was not consumed, so the input should be restored.
function refusalOutcome() {
  return { accepted: false };
}

/**
 * Map an attachment MIME to the matching ``Icon.jsx`` glyph name.
 * Falls back to the generic ``"file"`` glyph for unknown types so a
 * future MIME doesn't render as a blank tile.
 */
export function iconForMime(mime) {
  if (mime === "application/pdf") return "file-pdf";
  if (typeof mime === "string" && mime.startsWith("image/")) return "file-image";
  if (
    mime === "text/csv" ||
    mime === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
  ) {
    return "file-sheet";
  }
  if (mime === "text/plain" || mime === "text/markdown") return "file-text";
  return "file";
}

/**
 * Format ``bytes`` for chip display. Sub-MB stays in KB so single-page
 * PDFs and PNGs read sensibly; ≥1MB switches to a one-decimal MB to
 * match the 20MB cap the user sees in the AttachMenu hint.
 */
export function formatAttachmentSize(bytes) {
  if (bytes < 1024 * 1024) {
    return Math.max(1, Math.round(bytes / 1024)) + " KB";
  }
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

/**
 * Clip ``name`` to ``max`` chars with a trailing ellipsis so chips
 * stay readable. The full filename is mirrored into ``title=`` on the
 * chip so hover restores the original.
 */
export function truncateName(name, max = 20) {
  if (name.length <= max) return name;
  return name.slice(0, max - 1) + "…";
}

/**
 * Blocking-banner copy shown (#377) when one or more attachment chips
 * are stuck in the ``failed`` state. Send stays gated until the user
 * retries or removes every failed chip, so the message names both
 * affordances. Uses "attach" terminology per Channel's 2026-06-03 UI
 * copy convention — never "upload".
 */
export function failedAttachmentMessage(count) {
  const noun = count === 1 ? "attachment" : "attachments";
  const pronoun = count === 1 ? "it" : "them";
  return `${count} ${noun} failed to attach — retry or remove ${pronoun} to send.`;
}

/**
 * Auto-growing textarea + attach + model picker + mic + send. Translated
 * from design-sources/app/chat.jsx `Composer` function.
 *
 * Props:
 *   - model, effort, setModel, setEffort — for ModelPicker
 *   - onSend(text, atts) — invoked on submit; clears the input afterward
 *   - placeholder — defaults to "How can I help you today?"
 *   - autofocus — focuses the textarea on mount when true
 *
 * Attachments (#177): files arrive from the AttachMenu picker or via
 * drag-and-drop onto the composer wrap. Each accepted file enters
 * ``atts`` as a pending chip ({status: "attaching"}) and runs the
 * presign → upload → finalize pipeline; the chip then flips to
 * ``attached`` (carrying the canonical Attachment id) or ``failed``
 * (retryable). User-facing strings use "attach"/"attached" per
 * Channel's 2026-06-03 UI copy convention — never "upload".
 *
 * Dictation (issue #147): when the browser ships the Web Speech API,
 * the mic button toggles a `SpeechRecognition` session. Results are
 * spliced into the textarea at the caret position the user had when
 * they tapped mic. On browsers without the API the mic button is not
 * rendered at all — no dead control.
 */
const Composer = forwardRef(function Composer(
  {
    model,
    effort,
    setModel,
    setEffort,
    onSend,
    autofocus,
    placeholder,
    mcpServers,
    mcpSettings,
    setMcpSettings,
  },
  ref,
) {
  const [text, setText] = useState("");
  const [atts, setAtts] = useState([]);
  const [attachError, setAttachError] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [dictating, setDictating] = useState(false);
  const taRef = useRef(null);
  const recognitionRef = useRef(null);
  const dictationAnchorRef = useRef(null);
  // Tracks dragenter/dragleave depth across nested elements so the
  // overlay doesn't flicker as the cursor moves between children.
  const dragCounterRef = useRef(0);
  // Holds the underlying File for each pending chip so a failed-state
  // retry can re-run the pipeline without forcing the user to re-pick.
  const fileRefsRef = useRef(new Map());
  const { sendOnEnter } = useChannelPrefs();
  const [speechSupported] = useState(() => getSpeechRecognition() != null);

  useImperativeHandle(ref, () => ({
    setText: (value) => {
      setText(value);
      requestAnimationFrame(grow);
    },
    focus: () => taRef.current?.focus(),
  }), []);

  function grow() {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
  }

  useEffect(function focusOnMount() {
    if (autofocus && taRef.current) taRef.current.focus();
  }, [autofocus]);

  useEffect(function stopRecognitionOnUnmount() {
    return function cleanup() {
      if (recognitionRef.current) {
        try {
          recognitionRef.current.stop();
        } catch {
          /* recognition may already be stopped — ignore */
        }
        recognitionRef.current = null;
      }
    };
  }, []);

  function stopDictation() {
    if (recognitionRef.current) {
      try {
        recognitionRef.current.stop();
      } catch {
        /* already stopped — ignore */
      }
    }
    setDictating(false);
  }

  function startDictation() {
    const SpeechRecognition = getSpeechRecognition();
    const ta = taRef.current;
    const caret = ta.selectionStart ?? ta.value.length;
    dictationAnchorRef.current = {
      prefix: text.slice(0, caret),
      suffix: text.slice(caret),
    };
    let recognition;
    try {
      recognition = new SpeechRecognition();
    } catch {
      return;
    }
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.onresult = function handleResult(event) {
      let transcript = "";
      for (let i = 0; i < event.results.length; i += 1) {
        transcript += event.results[i][0].transcript;
      }
      const anchor = dictationAnchorRef.current;
      const next = anchor.prefix + transcript + anchor.suffix;
      setText(next);
      requestAnimationFrame(grow);
    };
    recognition.onend = function handleEnd() {
      setDictating(false);
      recognitionRef.current = null;
      dictationAnchorRef.current = null;
    };
    recognition.onerror = function handleError() {
      setDictating(false);
      recognitionRef.current = null;
      dictationAnchorRef.current = null;
    };
    recognitionRef.current = recognition;
    try {
      recognition.start();
      setDictating(true);
    } catch {
      recognitionRef.current = null;
      setDictating(false);
    }
  }

  function toggleDictation() {
    if (dictating) {
      stopDictation();
    } else {
      startDictation();
    }
  }

  // ---- attachments: validate + pipeline + retry --------------------

  function handleFiles(fileList) {
    setAttachError(null);
    if (!fileList) return;
    const incoming = Array.from(fileList);
    if (incoming.length === 0) return;
    if (atts.length + incoming.length > MAX_FILES_PER_MESSAGE) {
      setAttachError(
        `Can't attach more than ${MAX_FILES_PER_MESSAGE} files per message.`,
      );
      return;
    }
    const accepted = [];
    for (const file of incoming) {
      if (!ALLOWED_MIME_SET.has(file.type)) {
        setAttachError(`${file.name}: file type not supported.`);
        continue;
      }
      if (file.size > MAX_SIZE_BYTES) {
        setAttachError(`${file.name}: file too large (max 20 MB).`);
        continue;
      }
      accepted.push(file);
    }
    if (accepted.length === 0) return;
    const newChips = accepted.map((file) => {
      const tempId = nextTempId();
      fileRefsRef.current.set(tempId, file);
      return {
        tempId,
        status: "attaching",
        name: file.name,
        mime: file.type,
        size_bytes: file.size,
      };
    });
    setAtts((prev) => [...prev, ...newChips]);
    for (const chip of newChips) {
      startAttach(chip.tempId);
    }
  }

  async function startAttach(tempId) {
    const file = fileRefsRef.current.get(tempId);
    try {
      const checksum_sha256 = await sha256Hex(file);
      const presigned = await presignAttachment({
        name: file.name,
        mime: file.type,
        size_bytes: file.size,
      });
      await uploadToPresigned(presigned.url, presigned.required_headers, file);
      const att = await finalizeAttachment({
        presign_token: presigned.presign_token,
        checksum_sha256,
      });
      // Adopt the canonical Attachment id (the only field downstream
      // actually needs); keep name/mime/size_bytes as the user's file
      // pre-finalize so the chip label stays stable across the status
      // flip. Production has these in sync — but unit tests stub the
      // finalize response with a generic Attachment, so this guard
      // also makes the test contract less brittle.
      setAtts((prev) =>
        prev.map((c) =>
          c.tempId === tempId ? { ...c, status: "attached", id: att.id } : c,
        ),
      );
    } catch (err) {
      const reason = err instanceof Error && err.message ? err.message : "Failed to attach";
      setAtts((prev) =>
        prev.map((c) =>
          c.tempId === tempId ? { ...c, status: "failed", error: reason } : c,
        ),
      );
    }
  }

  function retryAtt(tempId) {
    setAtts((prev) =>
      prev.map((c) => (c.tempId === tempId ? { ...c, status: "attaching", error: null } : c)),
    );
    startAttach(tempId);
  }

  function removeAtt(tempId) {
    fileRefsRef.current.delete(tempId);
    setAtts((prev) => prev.filter((c) => c.tempId !== tempId));
  }

  // ---- drop zone --------------------------------------------------

  function onDragEnter(e) {
    if (![...(e.dataTransfer?.types ?? [])].includes("Files")) return;
    e.preventDefault();
    dragCounterRef.current += 1;
    setDragOver(true);
  }

  function onDragOver(e) {
    if (![...(e.dataTransfer?.types ?? [])].includes("Files")) return;
    e.preventDefault();
  }

  function onDragLeave() {
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) setDragOver(false);
  }

  function onDrop(e) {
    e.preventDefault();
    dragCounterRef.current = 0;
    setDragOver(false);
    handleFiles(e.dataTransfer?.files);
  }

  // ---- submit -----------------------------------------------------

  const anyAttaching = atts.some((a) => a.status === "attaching");
  // #377: a chip stuck in `failed` is neither attaching (so it never
  // blocked canSend) nor attached (so it was dropped from sentAtts) —
  // the pre-fix gating let the message send attachment-less, silently
  // losing the file. Block send whenever any chip has not reached
  // `attached`, and surface the blocking banner below so the failure
  // is visible instead of dropped.
  const failedAtts = atts.filter((a) => a.status === "failed");
  const anyFailed = failedAtts.length > 0;
  const attachedOnly = atts.filter((a) => a.status === "attached");
  const canSend =
    !anyAttaching && !anyFailed && (text.trim() !== "" || attachedOnly.length > 0);

  async function submit() {
    if (!canSend) return;
    stopDictation();
    // canSend already guarantees text.trim() || attachedOnly.length > 0,
    // so the OR fallback always lands on a non-empty body.
    const body =
      text.trim() || "Take a look at the attached files.";
    const prevText = text;
    const sentAtts = attachedOnly;
    // Invoke synchronously (callers rely on onSend firing during the
    // click tick) but funnel a synchronous throw into the same
    // rejected-promise path as an async failure, so the
    // .catch(refusalOutcome) below restores the input either way
    // instead of letting submit() reject unhandled.
    let result;
    try {
      result = onSend(
        body,
        sentAtts.map((a) => ({ id: a.id })),
      );
    } catch (err) {
      result = Promise.reject(err);
    }
    setText("");
    setAtts([]);
    setAttachError(null);
    fileRefsRef.current.clear();
    requestAnimationFrame(grow);
    // #211: a send refused before streaming started (422 too-long, 413,
    // auth, network) resolves to `{ accepted: false }` (see
    // useChatStream.send). Restore the user's input so the paste isn't
    // lost — unless the user already typed or attached something new
    // while the refusal was in flight (don't clobber), and not for
    // user-initiated aborts (nothing to recover).
    const outcome = await Promise.resolve(result).catch(refusalOutcome);
    if (outcome && outcome.accepted === false && !outcome.aborted) {
      setText((current) => (current === "" ? prevText : current));
      setAtts((current) => (current.length === 0 ? sentAtts : current));
      requestAnimationFrame(grow);
    }
  }

  function onKeyDown(e) {
    if (e.key !== "Enter") return;
    const cmdOrCtrl = e.metaKey || e.ctrlKey;
    if (sendOnEnter) {
      if (e.shiftKey) return;
      e.preventDefault();
      submit();
    } else if (cmdOrCtrl) {
      e.preventDefault();
      submit();
    }
  }

  function onTextareaChange(e) {
    setText(e.target.value);
    grow();
  }

  return (
    <div
      className={"composer-wrap" + (dragOver ? " drag-over" : "")}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <div className="composer">
        {atts.length > 0 && (
          <div className="attaches">
            {atts.map((a) => (
              <div
                className={"chip attach-chip status-" + a.status}
                key={a.tempId}
                data-status={a.status}
                title={a.name}
              >
                <span className="tile">
                  {a.status === "attaching" ? (
                    <span className="spinner" aria-label="Attaching" />
                  ) : (
                    // MIME-typed glyph so a sidebar full of pending
                    // chips scans at a glance. Status colour still comes
                    // from the chip-border + tile-background CSS so the
                    // attached / failed state is unambiguous on top of
                    // the format hint.
                    <Icon name={iconForMime(a.mime)} size={15} />
                  )}
                </span>
                <span className="nm">{truncateName(a.name)}</span>
                <span className="sz">{formatAttachmentSize(a.size_bytes)}</span>
                {a.status === "failed" && (
                  <button
                    type="button"
                    className="retry"
                    title="Retry"
                    aria-label={`Retry attaching ${a.name}`}
                    onClick={() => retryAtt(a.tempId)}
                  >
                    <Icon name="refresh" size={13} />
                  </button>
                )}
                <span
                  className="x"
                  role="button"
                  aria-label={`Remove ${a.name}`}
                  onClick={() => removeAtt(a.tempId)}
                >
                  <Icon name="close" size={14} />
                </span>
              </div>
            ))}
          </div>
        )}
        {attachError && (
          <div className="attach-error" role="alert">
            {attachError}
          </div>
        )}
        {anyFailed && (
          <div className="attach-error" role="alert">
            {failedAttachmentMessage(failedAtts.length)}
          </div>
        )}
        <textarea
          ref={taRef}
          rows={1}
          placeholder={placeholder || "How can I help you today?"}
          value={text}
          onChange={onTextareaChange}
          onKeyDown={onKeyDown}
        />
        <div className="composer-row">
          <AttachMenu onFiles={handleFiles} />
          {mcpServers && (
            <MCPPicker
              servers={mcpServers}
              mode={mcpSettings?.mode || "inherit"}
              explicitIds={mcpSettings?.explicit_server_ids || []}
              onChange={setMcpSettings}
            />
          )}
          <div className="spacer" />
          <ModelPicker model={model} effort={effort} onModel={setModel} onEffort={setEffort} />
          {speechSupported && (
            <button
              type="button"
              className={"cbtn round" + (dictating ? " on" : "")}
              title={dictating ? "Stop dictation" : "Dictate"}
              aria-label={dictating ? "Stop dictation" : "Dictate"}
              aria-pressed={dictating}
              onClick={toggleDictation}
            >
              <Icon name="mic" size={18} />
            </button>
          )}
          <button
            type="button"
            className="send"
            disabled={!canSend}
            onClick={submit}
            title={
              anyAttaching
                ? "Waiting for attachments…"
                : anyFailed
                  ? failedAttachmentMessage(failedAtts.length)
                  : "Send"
            }
          >
            <Icon name="arrow-up" size={18} stroke={2} />
          </button>
        </div>
      </div>
      {dragOver && (
        <div className="drop-overlay" role="status" aria-live="polite">
          <span>Drop files to attach</span>
        </div>
      )}
    </div>
  );
});

export default Composer;
