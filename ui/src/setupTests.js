// Copyright (c) 2026 John Carter. All rights reserved.
import "@testing-library/jest-dom";

// URL.createObjectURL and URL.revokeObjectURL are not implemented in jsdom.
// Stub them as vi.fn() so components that call them don't throw.
// Individual tests can call .mockReturnValue(...) to control the return.
globalThis.URL.createObjectURL = vi.fn(() => "blob:test-url");
globalThis.URL.revokeObjectURL = vi.fn();

// jsdom does not implement scrollIntoView; stub it so components that call it
// do not throw and coverage branches are reachable.
globalThis.HTMLElement.prototype.scrollIntoView = function () {};

// jsdom v24 ships localStorage but requires a --localstorage-file path to back
// it. Without a valid path the object exists but its methods are no-ops or
// missing. Replace it with a plain in-memory implementation so all tests get
// a consistent, fully functional Storage API without needing per-test stubs.
// Individual tests that need isolated state should use vi.stubGlobal("localStorage", ...)
// in their own beforeEach/afterEach (which overrides this shared instance).
(function patchLocalStorage() {
  let _store = Object.create(null);
  const storage = {
    getItem: (k) => Object.prototype.hasOwnProperty.call(_store, k) ? _store[k] : null,
    setItem: (k, v) => { _store[String(k)] = String(v); },
    removeItem: (k) => { delete _store[k]; },
    clear: () => { _store = Object.create(null); },
    get length() { return Object.keys(_store).length; },
    key: (i) => Object.keys(_store)[i] ?? null,
  };
  try {
    Object.defineProperty(globalThis, "localStorage", { value: storage, writable: true, configurable: true });
  } catch {
    globalThis.localStorage = storage;
  }
})();

// useTheme reads `matchMedia("(prefers-color-scheme: dark)")` on first render.
// jsdom doesn't ship matchMedia, so stub a default-light response. Individual
// tests that need a different value (e.g. the useTheme suite) can override
// with `vi.stubGlobal("matchMedia", ...)`.
if (typeof globalThis.matchMedia === "undefined") {
  globalThis.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}
