// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import Icon from "../components/Icon.jsx";
import { EFFORTS, MODELS } from "./data.js";

/**
 * Composer popover for picking the active model + reasoning effort.
 * Translated from design-sources/app/chat.jsx `ModelPicker` function.
 */
export default function ModelPicker({ model, effort, onModel, onEffort }) {
  const [open, setOpen] = useState(false);

  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        className="model-pick"
        onClick={() => setOpen((o) => !o)}
      >
        {model.short || model.name}
        <span className="eff">{effort}</span>
        <Icon name="chevron-down" size={14} />
      </button>
      {open && (
        <>
          <div className="backdrop" onClick={() => setOpen(false)} />
          <div className="pop" style={{ bottom: "calc(100% + 8px)", left: 0 }}>
            <div className="pop-h">Model</div>
            {MODELS.map((m) => (
              <div className="opt" key={m.id} onClick={() => { onModel(m); }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className="nm">{m.name}</span>
                    <span className="tg">{m.tier}</span>
                  </div>
                  <div className="ds">{m.desc}</div>
                </div>
                {m.id === model.id && (
                  <span className="ck"><Icon name="check" size={16} /></span>
                )}
              </div>
            ))}
            <div className="pop-h" style={{ marginTop: 4 }}>Reasoning effort</div>
            <div className="seg">
              {EFFORTS.map((e) => (
                <button
                  key={e}
                  type="button"
                  className={e === effort ? "on" : ""}
                  onClick={() => onEffort(e)}
                >
                  {e}
                </button>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
