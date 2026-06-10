// Copyright (c) 2026 John Carter. All rights reserved.
import React, { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  deleteMCPServer,
  listMCPServers,
  patchMCPServer,
  reauthMCPServer,
} from "../../api.js";
import Icon from "../../components/Icon.jsx";
import { useChannelPrefs } from "../../hooks/useChannelPrefs.js";
import AddMCPServerModal from "../AddMCPServerModal.jsx";
import { EFFORTS, cachedModels, loadModels, mergeWithDisplayMeta } from "../data.js";

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

// Each row: [label, hint, hook getter key, hook setter key]. The hook
// exposes booleans + boolean setters so we just thread the keys through.
const BEHAVIOR_ROWS = [
  [
    "Send on Enter",
    "Press Enter to send, Shift+Enter for a new line.",
    "sendOnEnter",
    "setSendOnEnter",
  ],
  [
    "Show reasoning trace",
    "Display the model's thinking before each reply.",
    "showReasoning",
    "setShowReasoning",
  ],
  [
    "Suggest follow-ups",
    "Offer related prompts after responses.",
    "suggestFollowups",
    "setSuggestFollowups",
  ],
];

/**
 * `/app/customize` view. Translated from design-sources/app/views.jsx
 * `SettingsView`. Five visual controls wired to useChannelPrefs (theme,
 * accent, density, default model, default effort) — each setter
 * immediately persists to localStorage via the hook's shared store, so
 * changes apply instantly across every mounted consumer.
 *
 * The model row sources its allowlist from `GET /api/models` via
 * `loadModels()` (issue #148 dropped the hardcoded fallback). Until
 * the API resolves we render a single "Loading models…" placeholder.
 */
export default function Customize() {
  const prefs = useChannelPrefs();
  const [models, setModels] = useState(() => cachedModels());
  const [modelsError, setModelsError] = useState(false);

  useEffect(function fetchModelsOnMount() {
    setModelsError(false);
    loadModels()
      .then(setModels)
      .catch(function onModelsFetchError() { setModelsError(true); });
  }, []);

  const [mcpServers, setMcpServers] = useState([]);
  const [mcpLoading, setMcpLoading] = useState(true);
  const [mcpError, setMcpError] = useState("");
  const [showAddMCP, setShowAddMCP] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();

  const refreshMCP = useCallback(async () => {
    setMcpLoading(true);
    setMcpError("");
    try {
      const { servers } = await listMCPServers();
      setMcpServers(servers);
    } catch (e) {
      console.error("listMCPServers", e);
      setMcpError("Couldn't load MCP servers.");
    } finally {
      setMcpLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshMCP();
  }, [refreshMCP]);

  // Refresh after the OAuth round-trip lands us back here.
  useEffect(() => {
    if (searchParams.get("mcp_authed")) {
      refreshMCP();
      const next = new URLSearchParams(searchParams);
      next.delete("mcp_authed");
      next.delete("server_id");
      next.delete("reason");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams, refreshMCP]);

  async function toggleGlobal(server) {
    await patchMCPServer(server.server_id, {
      globally_enabled: !server.globally_enabled,
    });
    refreshMCP();
  }

  async function removeServer(server) {
    if (!window.confirm(`Remove ${server.name}?`)) return;
    await deleteMCPServer(server.server_id);
    refreshMCP();
  }

  async function reauth(server) {
    const { auth_start_url } = await reauthMCPServer(server.server_id);
    window.open(auth_start_url, "_blank", "noopener,noreferrer");
  }

  // The hook stores `model` as an id string; the seg-ctl needs the model
  // object for its `desc` hint and `short` label.
  const activeModel =
    (models && models.find((m) => m.id === prefs.model)) ||
    (models && models[0]) ||
    mergeWithDisplayMeta({ id: prefs.model });

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
              {models == null && !modelsError && (
                <div className="hint" data-testid="models-loading">Loading models…</div>
              )}
              {modelsError && (
                <div className="hint" data-testid="models-error">Couldn't load models.</div>
              )}
              {models != null && (
                <div className="seg-ctl">
                  {models.map((m) => (
                    <button
                      type="button"
                      key={m.id}
                      className={m.id === activeModel.id ? "on" : ""}
                      onClick={() => prefs.setModel(m.id)}
                    >
                      {m.short}
                    </button>
                  ))}
                </div>
              )}
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
          {BEHAVIOR_ROWS.map(([lbl, hint, getKey, setKey]) => (
            <BehaviorRow
              key={lbl}
              lbl={lbl}
              hint={hint}
              on={prefs[getKey]}
              onToggle={() => prefs[setKey](!prefs[getKey])}
            />
          ))}
        </div>

        <div className="set-group">
          <h3>MCP servers</h3>
          <p className="hint">
            Connect external Model Context Protocol servers that this account
            can call from any chat. Toggle off to keep a server registered
            without making its tools available by default.
          </p>
          {mcpError && (
            <div className="hint" role="alert">{mcpError}</div>
          )}
          {mcpLoading ? (
            <div className="hint">Loading…</div>
          ) : mcpServers.length === 0 ? (
            <div className="hint">No MCP servers yet.</div>
          ) : (
            <ul className="mcp-list">
              {mcpServers.map((s) => (
                <li key={s.server_id} className="mcp-row">
                  <div className="mcp-row-main">
                    <div className="mcp-row-name">
                      <span className={`mcp-dot mcp-dot-${s.auth_status}`} />
                      {s.name}
                    </div>
                    <div className="mcp-row-url">{s.url}</div>
                  </div>
                  <div className="mcp-row-actions">
                    <button
                      type="button"
                      className={"toggle" + (s.globally_enabled ? " on" : "")}
                      onClick={() => toggleGlobal(s)}
                      aria-label={
                        s.globally_enabled
                          ? `Disable ${s.name} globally`
                          : `Enable ${s.name} globally`
                      }
                    >
                      <span className="knob" />
                    </button>
                    <button
                      type="button"
                      className="ck"
                      onClick={() => reauth(s)}
                    >
                      Reconnect
                    </button>
                    <button
                      type="button"
                      className="ck"
                      onClick={() => removeServer(s)}
                    >
                      Remove
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div className="set-row">
            <div />
            <div className="ctl">
              <button
                type="button"
                className="btn-primary"
                onClick={() => setShowAddMCP(true)}
              >
                Add server
              </button>
            </div>
          </div>
        </div>
        <AddMCPServerModal
          open={showAddMCP}
          onClose={() => setShowAddMCP(false)}
          onRegistered={() => refreshMCP()}
        />
      </div>
    </div>
  );
}

function BehaviorRow({ lbl, hint, on, onToggle }) {
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
          onClick={onToggle}
        >
          <span className="knob" />
        </button>
      </div>
    </div>
  );
}
