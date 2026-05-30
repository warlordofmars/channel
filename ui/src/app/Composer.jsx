// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useRef, useState } from "react";
import Icon from "../components/Icon.jsx";
import AttachMenu from "./AttachMenu.jsx";
import ModelPicker from "./ModelPicker.jsx";

/**
 * Auto-growing textarea + attach + model picker + mic + send. Translated
 * from design-sources/app/chat.jsx `Composer` function.
 *
 * Props:
 *   - model, effort, setModel, setEffort — for ModelPicker
 *   - onSend(text, atts) — invoked on submit; clears the input afterward
 *   - placeholder — defaults to "How can I help you today?"
 *   - autofocus — focuses the textarea on mount when true
 */
export default function Composer({ model, effort, setModel, setEffort, onSend, autofocus, placeholder }) {
  const [text, setText] = useState("");
  const [atts, setAtts] = useState([]);
  const taRef = useRef(null);

  function grow() {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
  }

  useEffect(() => {
    if (autofocus && taRef.current) taRef.current.focus();
  }, [autofocus]);

  function submit() {
    if (!text.trim() && atts.length === 0) return;
    onSend(text.trim() || "Take a look at the attached files.", atts);
    setText("");
    setAtts([]);
    requestAnimationFrame(grow);
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
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
                <span className="x" onClick={() => setAtts(atts.filter((_, j) => j !== i))}>
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
          onChange={(e) => { setText(e.target.value); grow(); }}
          onKeyDown={onKeyDown}
        />
        <div className="composer-row">
          <AttachMenu onAdd={(a) => setAtts((p) => [...p, a])} />
          <div className="spacer" />
          <ModelPicker model={model} effort={effort} onModel={setModel} onEffort={setEffort} />
          <button type="button" className="cbtn round" title="Dictate">
            <Icon name="mic" size={18} />
          </button>
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
}
