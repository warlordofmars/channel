// Copyright (c) 2026 John Carter. All rights reserved.
import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import Icon from "../components/Icon.jsx";
import { useChannelPrefs } from "../hooks/useChannelPrefs.js";
import AttachMenu from "./AttachMenu.jsx";
import ModelPicker from "./ModelPicker.jsx";

/**
 * Feature-detect the Web Speech API. Browsers that ship it expose
 * either the standardised `SpeechRecognition` constructor or the
 * vendor-prefixed `webkitSpeechRecognition` (Chrome, Edge, Safari).
 * Returns `null` on Firefox + everywhere the API isn't available.
 */
function getSpeechRecognition() {
  return window.SpeechRecognition ?? window.webkitSpeechRecognition ?? null;
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
 * Dictation (issue #147): when the browser ships the Web Speech API,
 * the mic button toggles a `SpeechRecognition` session. Results are
 * spliced into the textarea at the caret position the user had when
 * they tapped mic. On browsers without the API the mic button is not
 * rendered at all — no dead control.
 */
const Composer = forwardRef(function Composer(
  { model, effort, setModel, setEffort, onSend, autofocus, placeholder },
  ref,
) {
  const [text, setText] = useState("");
  const [atts, setAtts] = useState([]);
  const [dictating, setDictating] = useState(false);
  const taRef = useRef(null);
  const recognitionRef = useRef(null);
  const dictationAnchorRef = useRef(null);
  const { sendOnEnter } = useChannelPrefs();
  const [speechSupported] = useState(() => getSpeechRecognition() != null);

  // Imperative handle for parent components that need to seed the
  // textarea from outside (e.g. follow-up chip clicks). Exposes only
  // ``setText`` + ``focus`` — the surface stays intentionally tiny so
  // tests can't accidentally rely on internals.
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

  // Stop any active recognition on unmount so the user's mic isn't
  // held open after navigating away.
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
    // The button only renders when speechSupported is true, so
    // getSpeechRecognition() is guaranteed to return a constructor here.
    const SpeechRecognition = getSpeechRecognition();
    // Snapshot the caret position the user had when they tapped mic
    // so the transcript splices in at that point rather than at the
    // end of whatever text they (or another transcript) appended in
    // the meantime.
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
      // Some browsers throw on construction if permission is denied
      // up-front — degrade silently.
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
      // start() throws if recognition is already running on this
      // instance — collapse back to idle so the user can retry.
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

  function submit() {
    if (!text.trim() && atts.length === 0) return;
    stopDictation();
    onSend(text.trim() || "Take a look at the attached files.", atts);
    setText("");
    setAtts([]);
    requestAnimationFrame(grow);
  }

  function onKeyDown(e) {
    if (e.key !== "Enter") return;
    const cmdOrCtrl = e.metaKey || e.ctrlKey;
    if (sendOnEnter) {
      // Enter submits; Shift+Enter inserts a newline.
      if (e.shiftKey) return;
      e.preventDefault();
      submit();
    } else if (cmdOrCtrl) {
      // Cmd/Ctrl+Enter submits; plain Enter falls through to insert
      // a newline in the textarea (default browser behavior).
      e.preventDefault();
      submit();
    }
  }

  function onTextareaChange(e) {
    setText(e.target.value);
    grow();
  }

  function onChipRemove(index) {
    setAtts(atts.filter((_, j) => j !== index));
  }

  function onAttach(item) {
    setAtts(function appendChip(prev) { return [...prev, item]; });
  }

  return (
    <div className="composer-wrap">
      <div className="composer">
        {atts.length > 0 && (
          <div className="attaches">
            {atts.map((a, i) => (
              <div className="chip" key={i}>
                <span className="tile"><Icon name={a.ic} size={15} /></span>
                <span>{a.name}</span>
                <span className="x" onClick={() => onChipRemove(i)}>
                  <Icon name="close" size={14} />
                </span>
              </div>
            ))}
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
          <AttachMenu onAdd={onAttach} />
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
            disabled={!text.trim() && atts.length === 0}
            onClick={submit}
            title="Send"
          >
            <Icon name="arrow-up" size={18} stroke={2} />
          </button>
        </div>
      </div>
    </div>
  );
});

export default Composer;
