// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useState } from "react";
import Icon from "../../components/Icon.jsx";
import { useChannelPrefs } from "../../hooks/useChannelPrefs.js";
import { EFFORTS, MODELS } from "../data.js";

// Five accent hues. Co-located here because nothing else in the app reads
// them; the spec mock-data list doesn't include ACCENTS. Lifted verbatim
// from design-sources/app/views.jsx.
const ACCENTS = [
  { h: 42, label: "Clay" },
  { h: 18, label: "Rust" },
  { h: 150, label: "Fern" },
  { h: 235, label: "Slate" },
  { h: 300, label: "Plum" },
];

const BEHAVIOR_ROWS = [
  ["Send on Enter", "Press Enter to send, Shift+Enter for a new line.", true],
  ["Show reasoning trace", "Display the model's thinking before each reply.", false],
  ["Suggest follow-ups", "Offer related prompts after responses.", true],
];

/**
 * `/app/customize` view. Translated from design-sources/app/views.jsx
 * `SettingsView`. Five visual controls wired to useChannelPrefs (theme,
 * accent, density, default model, default effort) — each setter
 * immediately persists to localStorage via the hook's shared store, so
 * changes apply instantly across every mounted consumer.
 *
 * Three Behavior toggles are UI-only at Phase 6f per the spec — they flip
 * a local useState but don't propagate anywhere.
 */
export default function Customize() {
  const prefs = useChannelPrefs();
  // The hook stores `model` as an id string; the seg-ctl needs the model
  // object for its `desc` hint and `short` label.
  const activeModel = MODELS.find((m) => m.id === prefs.model) ?? MODELS[0];

  return (
    <div className="view">
      <div className="view-inner" style={{ maxWidth: 720 }}>
        <div className="view-head">
          <h2>Customize</h2>
          <p>Tune Channel's appearance and defaults. Changes apply instantly.</p>
        </div>

        <div className="set-group">
          <h3>Appearance</h3>
          <div className="set-row">
            <div>
              <div className="lbl">Theme</div>
              <div className="hint">Switch between light and dark surfaces.</div>
            </div>
            <div className="ctl">
              <div className="seg-ctl">
                <button
                  type="button"
                  className={prefs.theme === "light" ? "on" : ""}
                  onClick={() => prefs.setTheme("light")}
                >
                  <Icon name="sun" size={15} /> Light
                </button>
                <button
                  type="button"
                  className={prefs.theme === "dark" ? "on" : ""}
                  onClick={() => prefs.setTheme("dark")}
                >
                  <Icon name="moon" size={15} /> Dark
                </button>
              </div>
            </div>
          </div>
          <div className="set-row">
            <div>
              <div className="lbl">Accent color</div>
              <div className="hint">Used for highlights, actions, and the mark.</div>
            </div>
            <div className="ctl">
              <div className="swatches">
                {ACCENTS.map((a) => (
                  <button
                    type="button"
                    key={a.h}
                    className={"swatch" + (Number(prefs.accent) === a.h ? " on" : "")}
                    style={{ background: `oklch(0.60 0.13 ${a.h})` }}
                    title={a.label}
                    onClick={() => prefs.setAccent(String(a.h))}
                  />
                ))}
              </div>
            </div>
          </div>
          <div className="set-row">
            <div>
              <div className="lbl">Density</div>
              <div className="hint">Comfortable spacing or a tighter layout.</div>
            </div>
            <div className="ctl">
              <div className="seg-ctl">
                <button
                  type="button"
                  className={prefs.density === "cozy" ? "on" : ""}
                  onClick={() => prefs.setDensity("cozy")}
                >
                  Cozy
                </button>
                <button
                  type="button"
                  className={prefs.density === "compact" ? "on" : ""}
                  onClick={() => prefs.setDensity("compact")}
                >
                  Compact
                </button>
              </div>
            </div>
          </div>
        </div>

        <div className="set-group">
          <h3>Defaults</h3>
          <div className="set-row">
            <div>
              <div className="lbl">Default model</div>
              <div className="hint">{activeModel.desc}</div>
            </div>
            <div className="ctl">
              <div className="seg-ctl">
                {MODELS.map((m) => (
                  <button
                    type="button"
                    key={m.id}
                    className={m.id === activeModel.id ? "on" : ""}
                    onClick={() => prefs.setModel(m.id)}
                  >
                    {/* v8 ignore start */}
                    {m.short || m.name}
                    {/* v8 ignore stop */}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <div className="set-row">
            <div>
              <div className="lbl">Reasoning effort</div>
              <div className="hint">Higher effort thinks longer before answering.</div>
            </div>
            <div className="ctl">
              <div className="seg-ctl">
                {EFFORTS.map((e) => (
                  <button
                    type="button"
                    key={e}
                    className={e === prefs.effort ? "on" : ""}
                    onClick={() => prefs.setEffort(e)}
                  >
                    {e}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="set-group">
          <h3>Behavior</h3>
          {BEHAVIOR_ROWS.map(([lbl, hint, def]) => (
            <BehaviorRow key={lbl} lbl={lbl} hint={hint} def={def} />
          ))}
        </div>
      </div>
    </div>
  );
}

function BehaviorRow({ lbl, hint, def }) {
  const [on, setOn] = useState(def);
  return (
    <div className="set-row">
      <div>
        <div className="lbl">{lbl}</div>
        <div className="hint">{hint}</div>
      </div>
      <div className="ctl">
        <button
          type="button"
          className={"toggle" + (on ? " on" : "")}
          onClick={() => setOn((o) => !o)}
        >
          <span className="knob" />
        </button>
      </div>
    </div>
  );
}
