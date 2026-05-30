// Copyright (c) 2026 John Carter. All rights reserved.
import { useCallback, useEffect, useState } from "react";

export const STORAGE_KEYS = Object.freeze({
  theme:     "channel-theme",
  siteTheme: "channel-site-theme",
  accent:    "channel-accent",
  density:   "channel-density",
  shape:     "channel-shape",
  font:      "channel-font",
  model:     "channel-model",
  effort:    "channel-effort",
});

export const DEFAULTS = Object.freeze({
  theme:     "dark",
  siteTheme: "light",
  accent:    "42",
  density:   "cozy",
  shape:     "soft",
  font:      "figtree",
  model:     "claude-opus-4-8",
  effort:    "High",
});

function readPref(key, fallback) {
  try {
    const stored = localStorage.getItem(key);
    return stored == null ? fallback : stored;
  } catch {
    return fallback;
  }
}

function applyAttr(name, value) {
  document.documentElement.setAttribute("data-" + name, value);
}

function writePref(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* localStorage may throw in private mode; degrade silently */
  }
}

export function useChannelPrefs() {
  const [theme, _setTheme] = useState(() => readPref(STORAGE_KEYS.theme, DEFAULTS.theme));
  const [siteTheme, _setSiteTheme] = useState(() => readPref(STORAGE_KEYS.siteTheme, DEFAULTS.siteTheme));
  const [accent, _setAccent] = useState(() => readPref(STORAGE_KEYS.accent, DEFAULTS.accent));
  const [density, _setDensity] = useState(() => readPref(STORAGE_KEYS.density, DEFAULTS.density));
  const [shape, _setShape] = useState(() => readPref(STORAGE_KEYS.shape, DEFAULTS.shape));
  const [font, _setFont] = useState(() => readPref(STORAGE_KEYS.font, DEFAULTS.font));
  const [model, _setModel] = useState(() => readPref(STORAGE_KEYS.model, DEFAULTS.model));
  const [effort, _setEffort] = useState(() => readPref(STORAGE_KEYS.effort, DEFAULTS.effort));

  useEffect(() => { applyAttr("theme", theme);     writePref(STORAGE_KEYS.theme, theme); }, [theme]);
  // siteTheme is persisted but not applied to <html> by this hook — the
  // marketing pages own their theme attribute (data-site-theme) so app and
  // site themes can be set independently on different DOM subtrees.
  useEffect(() => { writePref(STORAGE_KEYS.siteTheme, siteTheme); }, [siteTheme]);
  useEffect(() => { applyAttr("accent", accent);   writePref(STORAGE_KEYS.accent, accent); }, [accent]);
  useEffect(() => { applyAttr("density", density); writePref(STORAGE_KEYS.density, density); }, [density]);
  useEffect(() => { applyAttr("shape", shape);     writePref(STORAGE_KEYS.shape, shape); }, [shape]);
  useEffect(() => { applyAttr("font", font);       writePref(STORAGE_KEYS.font, font); }, [font]);
  useEffect(() => { applyAttr("model", model);     writePref(STORAGE_KEYS.model, model); }, [model]);
  useEffect(() => { applyAttr("effort", effort);   writePref(STORAGE_KEYS.effort, effort); }, [effort]);

  useEffect(() => {
    document.documentElement.style.setProperty("--accent-h", accent);
  }, [accent]);

  const toggleTheme = useCallback(() => _setTheme((t) => (t === "dark" ? "light" : "dark")), []);
  const toggleSiteTheme = useCallback(() => _setSiteTheme((t) => (t === "dark" ? "light" : "dark")), []);

  return {
    theme, setTheme: _setTheme, toggleTheme,
    siteTheme, setSiteTheme: _setSiteTheme, toggleSiteTheme,
    accent, setAccent: _setAccent,
    density, setDensity: _setDensity,
    shape, setShape: _setShape,
    font, setFont: _setFont,
    model, setModel: _setModel,
    effort, setEffort: _setEffort,
  };
}
