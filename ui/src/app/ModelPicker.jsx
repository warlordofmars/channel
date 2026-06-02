// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useEffect, useState } from "react";
import Icon from "../components/Icon.jsx";
import { EFFORTS, cachedModels, loadModels } from "./data.js";

/**
 * Composer popover for picking the active model + reasoning effort.
 * Translated from design-sources/app/chat.jsx `ModelPicker` function.
 *
 * The list of models comes from `GET /api/models` via `loadModels()`
 * (issue #148 dropped the hardcoded fallback). Until the API resolves
 * we render a single "Loading models…" row rather than a stale list.
 * If the API fails outright we render "Couldn't load models" and a
 * retry affordance — the picker is intentionally empty so the user
 * can't pick a stale id.
 */
export default function ModelPicker({ model, effort, onModel, onEffort }) {
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState(() => cachedModels());
  const [error, setError] = useState(false);

  useEffect(function fetchModelsOnMount() {
    fetchModels();
  }, []);

  function fetchModels() {
    setError(false);
    loadModels()
      .then((next) => setModels(next))
      .catch(() => setError(true));
  }

  function togglePopover() {
    setOpen((o) => !o);
  }

  function closePopover() {
    setOpen(false);
  }

  return (
    <div style={{ position: "relative" }}>
      <button
        type="button"
        className="model-pick"
        onClick={togglePopover}
      >
        {model.short || model.name}
        <span className="eff">{effort}</span>
        <Icon name="chevron-down" size={14} />
      </button>
      {open && (
        <>
          <div className="backdrop" onClick={closePopover} />
          <div className="pop" style={{ bottom: "calc(100% + 8px)", left: 0 }}>
            <div className="pop-h">Model</div>
            {models == null && !error && (
              <div className="opt" data-testid="models-loading">
                <div style={{ flex: 1 }}>
                  <div className="ds">Loading models…</div>
                </div>
              </div>
            )}
            {error && (
              <div className="opt" data-testid="models-error">
                <div style={{ flex: 1 }}>
                  <div className="ds">Couldn't load models.</div>
                </div>
                <button
                  type="button"
                  className="ck"
                  onClick={fetchModels}
                >
                  Retry
                </button>
              </div>
            )}
            {models != null && models.map((m) => (
              <div
                className="opt"
                key={m.id}
                onClick={() => onModel(m)}
              >
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
