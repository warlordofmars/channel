// Copyright (c) 2026 John Carter. All rights reserved.
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  APP_VH_ATTRIBUTE,
  APP_VH_MIN_SHORTFALL_PX,
  APP_VH_PROPERTY,
  measureAppViewport,
  startAppViewportSync,
  syncAppViewport,
} from "./appViewport.js";

/**
 * jsdom's own `innerHeight` accessor, captured before any test replaces
 * it. `Object.defineProperty` overwrites the accessor with a plain value
 * property, and neither `vi.unstubAllGlobals` nor `vi.restoreAllMocks`
 * undoes that — so without restoring the original descriptor the
 * override outlives the suite and any later test that spies on the
 * getter would silently see a value property instead.
 */
const ORIGINAL_INNER_HEIGHT = Object.getOwnPropertyDescriptor(window, "innerHeight");

/**
 * jsdom performs no layout, so `documentElement.clientHeight` is a
 * hard-coded 0 and `innerHeight` a hard-coded 768. Both are redefined
 * per test — the module's entire behaviour is a comparison between them,
 * so leaving either at its default would test the harness, not the code.
 */
function setViewport({ windowHeight, layoutHeight }) {
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    value: windowHeight,
  });
  Object.defineProperty(document.documentElement, "clientHeight", {
    configurable: true,
    value: layoutHeight,
  });
}

function pinned() {
  const root = document.documentElement;
  return {
    property: root.style.getPropertyValue(APP_VH_PROPERTY),
    attribute: root.hasAttribute(APP_VH_ATTRIBUTE),
  };
}

afterEach(() => {
  const root = document.documentElement;
  root.style.removeProperty(APP_VH_PROPERTY);
  root.removeAttribute(APP_VH_ATTRIBUTE);
  delete document.documentElement.clientHeight;
  Object.defineProperty(window, "innerHeight", ORIGINAL_INNER_HEIGHT);
  vi.restoreAllMocks();
});

describe("measureAppViewport", () => {
  it("reports the window height and the layout-viewport height", () => {
    setViewport({ windowHeight: 852, layoutHeight: 756 });

    expect(measureAppViewport()).toEqual({ windowHeight: 852, layoutHeight: 756 });
  });
});

describe("syncAppViewport", () => {
  it("pins the true window height when the layout viewport is short", () => {
    // The installed-iOS-PWA shape: the window is the full 852pt screen
    // but `inset: 0` / `100dvh` resolve against a 756pt layout viewport,
    // leaving the 96pt dead strip #467 is about.
    setViewport({ windowHeight: 852, layoutHeight: 756 });

    expect(syncAppViewport()).toBe(852);
    expect(pinned()).toEqual({ property: "852px", attribute: true });
  });

  it("publishes nothing when the two heights agree (every desktop engine)", () => {
    setViewport({ windowHeight: 900, layoutHeight: 900 });

    expect(syncAppViewport()).toBeNull();
    expect(pinned()).toEqual({ property: "", attribute: false });
  });

  it("never shrinks the layer when the window is the smaller of the two", () => {
    // Mobile Safari: `innerHeight` is the visual viewport (toolbars
    // overlay it) while `clientHeight` is the larger layout viewport.
    // Pinning here would visibly shorten `.stage` — the grow-only rule
    // is what keeps in-browser rendering byte-identical.
    setViewport({ windowHeight: 745, layoutHeight: 852 });

    expect(syncAppViewport()).toBeNull();
    expect(pinned()).toEqual({ property: "", attribute: false });
  });

  it("ignores a sub-pixel disagreement", () => {
    setViewport({
      windowHeight: 900,
      layoutHeight: 900 - APP_VH_MIN_SHORTFALL_PX / 2,
    });

    expect(syncAppViewport()).toBeNull();
    expect(pinned().attribute).toBe(false);
  });

  it("withdraws a previous pin once the shortfall closes", () => {
    setViewport({ windowHeight: 852, layoutHeight: 756 });
    syncAppViewport();
    expect(pinned().attribute).toBe(true);

    setViewport({ windowHeight: 852, layoutHeight: 852 });

    expect(syncAppViewport()).toBeNull();
    expect(pinned()).toEqual({ property: "", attribute: false });
  });
});

describe("startAppViewportSync", () => {
  it("measures immediately and re-measures on every viewport event", () => {
    setViewport({ windowHeight: 852, layoutHeight: 756 });

    const stop = startAppViewportSync();
    expect(pinned().property).toBe("852px");

    setViewport({ windowHeight: 900, layoutHeight: 800 });
    window.dispatchEvent(new Event("resize"));
    expect(pinned().property).toBe("900px");

    setViewport({ windowHeight: 1000, layoutHeight: 800 });
    window.dispatchEvent(new Event("orientationchange"));
    expect(pinned().property).toBe("1000px");

    setViewport({ windowHeight: 1100, layoutHeight: 800 });
    window.dispatchEvent(new Event("pageshow"));
    expect(pinned().property).toBe("1100px");

    stop();
    setViewport({ windowHeight: 1200, layoutHeight: 800 });
    window.dispatchEvent(new Event("resize"));
    expect(pinned().property).toBe("1100px");
  });

  it("subscribes to visualViewport when the browser provides one", () => {
    // jsdom ships no `visualViewport`, which is what covers the absent
    // branch in the test above; this one supplies a stub so the iOS
    // keyboard path (where only `visualViewport` fires) is exercised.
    const listeners = {};
    vi.stubGlobal("visualViewport", {
      addEventListener: vi.fn((type, fn) => { listeners[type] = fn; }),
      removeEventListener: vi.fn(),
    });
    setViewport({ windowHeight: 852, layoutHeight: 756 });

    const stop = startAppViewportSync();
    expect(window.visualViewport.addEventListener).toHaveBeenCalledWith(
      "resize",
      expect.any(Function),
    );

    setViewport({ windowHeight: 852, layoutHeight: 700 });
    listeners.resize();
    expect(pinned().property).toBe("852px");

    stop();
    expect(window.visualViewport.removeEventListener).toHaveBeenCalledWith(
      "resize",
      expect.any(Function),
    );
    vi.unstubAllGlobals();
  });
});
