// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LayoutDebug, {
  CHAIN_PROPS,
  LAYOUT_DEBUG_EVENT,
  LAYOUT_DEBUG_PARAM,
  LAYOUT_DEBUG_STORAGE_KEY,
  LAYOUT_DEBUG_TAP_COUNT,
  LAYOUT_DEBUG_TAP_WINDOW_MS,
  SAFE_AREA_SIDES,
  collectChain,
  collectSnapshot,
  describeElement,
  disarmLayoutDebug,
  elementLabel,
  findChainRoot,
  isLayoutDebugArmed,
  isLayoutDebugParamRequested,
  isLayoutDebugRequested,
  matchesDisplayMode,
  readSafeAreaInsets,
  round,
  __resetLayoutDebugFallbackForTest,
  subscribeLayoutDebug,
  toggleLayoutDebugArmed,
  useLayoutDebugRequested,
  useLayoutDebugTapGesture,
} from "./LayoutDebug.jsx";
import { APP_VH_ATTRIBUTE, APP_VH_PROPERTY } from "../lib/appViewport.js";

/** Minimal stand-in for the Conversation view's composer chain. */
function mountComposerFixture() {
  const stage = document.createElement("div");
  stage.className = "stage full";
  const bottom = document.createElement("div");
  bottom.className = "bottom-composer";
  const composer = document.createElement("div");
  composer.className = "composer";
  bottom.appendChild(composer);
  stage.appendChild(bottom);
  document.body.appendChild(stage);
  return { stage, bottom, composer };
}

/**
 * jsdom's own `innerHeight` property descriptor. No vitest cleanup
 * undoes an `Object.defineProperty` write, so the original is captured
 * here and put back in `afterEach` — see the fuller note in
 * `lib/appViewport.test.js`.
 */
const ORIGINAL_INNER_HEIGHT = Object.getOwnPropertyDescriptor(window, "innerHeight");

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

afterEach(() => {
  document.body.replaceChildren();
  document.documentElement.style.removeProperty(APP_VH_PROPERTY);
  document.documentElement.removeAttribute(APP_VH_ATTRIBUTE);
  delete document.documentElement.clientHeight;
  Object.defineProperty(window, "innerHeight", ORIGINAL_INNER_HEIGHT);
  window.history.pushState({}, "", "/");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.removeItem(LAYOUT_DEBUG_STORAGE_KEY);
  __resetLayoutDebugFallbackForTest();
});

/** Stand-in for Shell's brand mark — the gesture's only real call site. */
function TapHarness() {
  const onTap = useLayoutDebugTapGesture();
  return <span data-testid="brand" onPointerDown={onTap} />;
}

/** Renders whatever the reactive gate currently reports. */
function GateHarness() {
  const requested = useLayoutDebugRequested();
  return <span data-testid="gate">{String(requested)}</span>;
}

function tapBrand(times) {
  const brand = screen.getByTestId("brand");
  for (let i = 0; i < times; i += 1) fireEvent.pointerDown(brand);
}

describe("isLayoutDebugRequested", () => {
  it("is true only for the exact opt-in value", () => {
    window.history.pushState({}, "", `/app?${LAYOUT_DEBUG_PARAM}=1`);
    expect(isLayoutDebugRequested()).toBe(true);
  });

  it("is false for any other value and when the parameter is absent", () => {
    window.history.pushState({}, "", `/app?${LAYOUT_DEBUG_PARAM}=0`);
    expect(isLayoutDebugRequested()).toBe(false);

    window.history.pushState({}, "", "/app");
    expect(isLayoutDebugRequested()).toBe(false);
  });
});

describe("round", () => {
  it("keeps one decimal place so sub-pixel gaps stay visible", () => {
    expect(round(123.456)).toBe(123.5);
    expect(round(0)).toBe(0);
  });
});

describe("readSafeAreaInsets", () => {
  it("reports every side and leaves no probe behind", () => {
    vi.spyOn(window, "getComputedStyle").mockReturnValue({
      getPropertyValue: (prop) => (prop === "padding-bottom" ? "34px" : "0px"),
    });
    const before = document.body.childElementCount;

    const insets = readSafeAreaInsets();

    expect(insets).toHaveLength(SAFE_AREA_SIDES.length);
    expect(insets).toContainEqual(["env(safe-area-inset-bottom)", "34px"]);
    expect(insets).toContainEqual(["env(safe-area-inset-top)", "0px"]);
    expect(document.body.childElementCount).toBe(before);
  });

  it("labels an unresolvable inset rather than rendering a blank", () => {
    // An engine that doesn't understand `env()` drops the declaration
    // and the computed padding reads back empty; a bare "" in the
    // readout would look like a measurement of zero.
    vi.spyOn(window, "getComputedStyle").mockReturnValue({
      getPropertyValue: () => "",
    });

    expect(readSafeAreaInsets()[0]).toEqual(["env(safe-area-inset-top)", "(empty)"]);
  });
});

describe("elementLabel", () => {
  it("joins every class onto the tag name", () => {
    const { bottom, stage } = mountComposerFixture();
    expect(elementLabel(bottom)).toBe("div.bottom-composer");
    expect(elementLabel(stage)).toBe("div.stage.full");
  });

  it("falls back to the bare tag when there is no usable class name", () => {
    expect(elementLabel(document.documentElement)).toBe("html");
    // SVG elements carry an SVGAnimatedString, not a string — the chain
    // only walks HTML ancestors today, but the guard keeps a stray one
    // from rendering "[object Object]".
    expect(elementLabel({ className: {}, tagName: "SVG" })).toBe("svg");
  });
});

describe("describeElement", () => {
  it("reports the box and every chain property", () => {
    const { composer } = mountComposerFixture();

    const described = describeElement(composer);

    expect(described.label).toBe("div.composer");
    expect(described.rect).toMatch(/^top=\d+(\.\d)? bottom=\d+(\.\d)? height=\d+(\.\d)?$/);
    CHAIN_PROPS.forEach((prop) => expect(described.styles).toContain(`${prop}:`));
  });
});

describe("findChainRoot", () => {
  it("prefers the Conversation composer", () => {
    const { composer } = mountComposerFixture();
    expect(findChainRoot()).toBe(composer);
  });

  it("falls through to .stage when no composer is on the page", () => {
    const stage = document.createElement("div");
    stage.className = "stage";
    document.body.appendChild(stage);
    expect(findChainRoot()).toBe(stage);
  });

  it("returns null when nothing matches", () => {
    expect(findChainRoot()).toBeNull();
  });
});

describe("collectChain", () => {
  it("walks from the start element up to html", () => {
    const { composer } = mountComposerFixture();

    const labels = collectChain(composer).map((entry) => entry.label);

    expect(labels).toEqual([
      "div.composer",
      "div.bottom-composer",
      "div.stage.full",
      "body",
      "html",
    ]);
  });
});

describe("matchesDisplayMode", () => {
  it("delegates to matchMedia", () => {
    const matchMedia = vi.fn(() => ({ matches: true }));
    vi.stubGlobal("matchMedia", matchMedia);

    expect(matchesDisplayMode("standalone")).toBe(true);
    expect(matchMedia).toHaveBeenCalledWith("(display-mode: standalone)");
  });
});

describe("collectSnapshot", () => {
  function rowValue(snapshot, label) {
    return snapshot.rows.find(([key]) => key === label)?.[1];
  }

  it("reports the shortfall, the applied pin, and the chain", () => {
    mountComposerFixture();
    setViewport({ windowHeight: 852, layoutHeight: 756 });
    document.documentElement.style.setProperty(APP_VH_PROPERTY, "852px");
    document.documentElement.setAttribute(APP_VH_ATTRIBUTE, "");

    const snapshot = collectSnapshot();

    expect(rowValue(snapshot, "window.innerHeight")).toBe("852");
    expect(rowValue(snapshot, "documentElement.clientHeight")).toBe("756");
    expect(rowValue(snapshot, "shortfall (inner − client)")).toBe("96");
    expect(rowValue(snapshot, APP_VH_PROPERTY)).toBe("852px");
    expect(rowValue(snapshot, `html[${APP_VH_ATTRIBUTE}]`)).toBe("true");
    expect(snapshot.chainRoot).toBe("div.composer");
    expect(snapshot.chain).toHaveLength(5);
  });

  it("marks visualViewport, the pin and the chain as absent when they are", () => {
    // jsdom ships no visualViewport, so this is the unstubbed default.
    setViewport({ windowHeight: 768, layoutHeight: 768 });

    const snapshot = collectSnapshot();

    expect(rowValue(snapshot, "visualViewport.height")).toBe("(absent)");
    expect(rowValue(snapshot, "visualViewport.offsetTop")).toBe("(absent)");
    expect(rowValue(snapshot, "visualViewport.scale")).toBe("(absent)");
    expect(rowValue(snapshot, APP_VH_PROPERTY)).toBe("(unset)");
    expect(snapshot.chainRoot).toBe("(nothing matched)");
    expect(snapshot.chain).toEqual([]);
  });

  it("reports visualViewport when the browser provides one", () => {
    vi.stubGlobal("visualViewport", {
      height: 653.25,
      offsetTop: 0,
      scale: 1,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    });

    const snapshot = collectSnapshot();

    expect(rowValue(snapshot, "visualViewport.height")).toBe("653.3");
    expect(rowValue(snapshot, "visualViewport.scale")).toBe("1");
  });
});

describe("<LayoutDebug />", () => {
  beforeEach(() => {
    mountComposerFixture();
    setViewport({ windowHeight: 852, layoutHeight: 756 });
  });

  it("renders the readout rows and the composer→html chain", () => {
    render(<LayoutDebug />);

    expect(screen.getByRole("region", { name: /layout debug/i })).toBeTruthy();
    expect(screen.getByText("window.innerHeight")).toBeTruthy();
    expect(screen.getByText("852")).toBeTruthy();
    expect(screen.getByText(/chain: div\.composer → html/)).toBeTruthy();
    expect(screen.getByText("div.bottom-composer")).toBeTruthy();
    expect(screen.getByText("html")).toBeTruthy();
  });

  it("re-measures on demand", () => {
    render(<LayoutDebug />);
    expect(screen.getByText("852")).toBeTruthy();

    setViewport({ windowHeight: 900, layoutHeight: 756 });
    fireEvent.click(screen.getByRole("button", { name: /re-measure/i }));

    expect(screen.getByText("900")).toBeTruthy();
  });

  it("re-measures on window resize and orientation change", () => {
    render(<LayoutDebug />);

    setViewport({ windowHeight: 901, layoutHeight: 756 });
    act(() => { window.dispatchEvent(new Event("resize")); });
    expect(screen.getByText("901")).toBeTruthy();

    setViewport({ windowHeight: 902, layoutHeight: 756 });
    act(() => { window.dispatchEvent(new Event("orientationchange")); });
    expect(screen.getByText("902")).toBeTruthy();
  });

  it("stops listening once unmounted", () => {
    const remove = vi.spyOn(window, "removeEventListener");

    render(<LayoutDebug />).unmount();

    expect(remove).toHaveBeenCalledWith("resize", expect.any(Function));
    expect(remove).toHaveBeenCalledWith("orientationchange", expect.any(Function));
  });

  it("tracks visualViewport resizes when the browser provides one", () => {
    const listeners = {};
    vi.stubGlobal("visualViewport", {
      height: 852,
      offsetTop: 0,
      scale: 1,
      addEventListener: vi.fn((type, fn) => { listeners[type] = fn; }),
      removeEventListener: vi.fn(),
    });

    const view = render(<LayoutDebug />);

    setViewport({ windowHeight: 903, layoutHeight: 756 });
    act(() => { listeners.resize(); });
    expect(screen.getByText("903")).toBeTruthy();

    view.unmount();
    expect(window.visualViewport.removeEventListener).toHaveBeenCalledWith(
      "resize",
      expect.any(Function),
    );
  });
});

describe("the persisted gesture gate (#504)", () => {
  it("is armed only by the exact stored value", () => {
    expect(isLayoutDebugArmed()).toBe(false);

    localStorage.setItem(LAYOUT_DEBUG_STORAGE_KEY, "0");
    expect(isLayoutDebugArmed()).toBe(false);

    localStorage.setItem(LAYOUT_DEBUG_STORAGE_KEY, "1");
    expect(isLayoutDebugArmed()).toBe(true);
  });

  it("reads as disarmed rather than throwing when storage is blocked", () => {
    // Safari with site data blocked throws on access. This runs during
    // App's render, so a throw would white-screen the app.
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(function blockedRead() {
        throw new Error("SecurityError");
      }),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });

    expect(isLayoutDebugArmed()).toBe(false);
    expect(localStorage.getItem).toHaveBeenCalledWith(LAYOUT_DEBUG_STORAGE_KEY);
  });

  it("ORs with the query parameter so the desktop path still works", () => {
    localStorage.setItem(LAYOUT_DEBUG_STORAGE_KEY, "1");
    expect(isLayoutDebugParamRequested()).toBe(false);
    expect(isLayoutDebugRequested()).toBe(true);

    localStorage.removeItem(LAYOUT_DEBUG_STORAGE_KEY);
    window.history.pushState({}, "", `/app?${LAYOUT_DEBUG_PARAM}=1`);
    expect(isLayoutDebugParamRequested()).toBe(true);
    expect(isLayoutDebugRequested()).toBe(true);
  });

  it("toggles the stored flag and announces every change", () => {
    const heard = vi.fn();
    window.addEventListener(LAYOUT_DEBUG_EVENT, heard);

    expect(toggleLayoutDebugArmed()).toBe(true);
    expect(localStorage.getItem(LAYOUT_DEBUG_STORAGE_KEY)).toBe("1");

    expect(toggleLayoutDebugArmed()).toBe(false);
    expect(localStorage.getItem(LAYOUT_DEBUG_STORAGE_KEY)).toBeNull();
    expect(heard).toHaveBeenCalledTimes(2);

    window.removeEventListener(LAYOUT_DEBUG_EVENT, heard);
  });

  it("really arms for the page's life when the write is refused", () => {
    // A blocked write leaves nothing for the next read to find, so
    // without the in-memory fallback the gate would report disarmed an
    // instant after the gesture reported success (Copilot, PR #505).
    const heard = vi.fn();
    window.addEventListener(LAYOUT_DEBUG_EVENT, heard);
    const setItem = vi.fn(function blockedWrite() {
      throw new Error("QuotaExceededError");
    });
    const removeItem = vi.fn();
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(function readsEmpty() {
        return null;
      }),
      setItem,
      removeItem,
    });

    expect(toggleLayoutDebugArmed()).toBe(true);
    expect(setItem).toHaveBeenCalledTimes(1);
    expect(heard).toHaveBeenCalledTimes(1);
    // The gate agrees, even though storage holds nothing.
    expect(isLayoutDebugArmed()).toBe(true);
    expect(isLayoutDebugRequested()).toBe(true);

    // And the gesture still disarms from the fallback.
    expect(toggleLayoutDebugArmed()).toBe(false);
    expect(isLayoutDebugArmed()).toBe(false);

    window.removeEventListener(LAYOUT_DEBUG_EVENT, heard);
  });

  it("hands authority back to storage once a write succeeds", () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(function readsEmpty() {
        return null;
      }),
      setItem: vi.fn(function blockedWrite() {
        throw new Error("QuotaExceededError");
      }),
      removeItem: vi.fn(),
    });
    expect(toggleLayoutDebugArmed()).toBe(true);
    expect(isLayoutDebugArmed()).toBe(true);

    // Storage recovers: the successful write clears the fallback, so a
    // stale in-memory value can't outlive the condition that caused it.
    vi.unstubAllGlobals();
    expect(toggleLayoutDebugArmed()).toBe(false);
    expect(localStorage.getItem(LAYOUT_DEBUG_STORAGE_KEY)).toBeNull();
    expect(isLayoutDebugArmed()).toBe(false);
  });
});

describe("subscribeLayoutDebug", () => {
  it("fires on this tab's toggle and on another tab's storage write", () => {
    const onChange = vi.fn();
    const unsubscribe = subscribeLayoutDebug(onChange);

    window.dispatchEvent(new Event(LAYOUT_DEBUG_EVENT));
    window.dispatchEvent(new Event("storage"));
    expect(onChange).toHaveBeenCalledTimes(2);

    unsubscribe();
    window.dispatchEvent(new Event(LAYOUT_DEBUG_EVENT));
    window.dispatchEvent(new Event("storage"));
    expect(onChange).toHaveBeenCalledTimes(2);
  });
});

describe("useLayoutDebugRequested", () => {
  it("re-renders on toggle, so arming needs no reload", () => {
    render(<GateHarness />);
    expect(screen.getByTestId("gate").textContent).toBe("false");

    act(() => { toggleLayoutDebugArmed(); });
    expect(screen.getByTestId("gate").textContent).toBe("true");

    act(() => { toggleLayoutDebugArmed(); });
    expect(screen.getByTestId("gate").textContent).toBe("false");
  });
});

describe("useLayoutDebugTapGesture", () => {
  it("toggles on the full tap count and ignores anything short of it", () => {
    render(<TapHarness />);

    tapBrand(LAYOUT_DEBUG_TAP_COUNT - 1);
    expect(isLayoutDebugArmed()).toBe(false);

    tapBrand(1);
    expect(isLayoutDebugArmed()).toBe(true);

    // The counter resets on toggle, so disarming needs a full gesture.
    tapBrand(LAYOUT_DEBUG_TAP_COUNT - 1);
    expect(isLayoutDebugArmed()).toBe(true);

    tapBrand(1);
    expect(isLayoutDebugArmed()).toBe(false);
  });

  it("drops taps that fall outside the window", () => {
    vi.useFakeTimers();
    render(<TapHarness />);

    tapBrand(LAYOUT_DEBUG_TAP_COUNT - 1);
    act(() => { vi.advanceTimersByTime(LAYOUT_DEBUG_TAP_WINDOW_MS + 1); });

    // The stale taps are discarded, so this run starts from zero.
    tapBrand(LAYOUT_DEBUG_TAP_COUNT - 1);
    expect(isLayoutDebugArmed()).toBe(false);

    tapBrand(1);
    expect(isLayoutDebugArmed()).toBe(true);

    vi.useRealTimers();
  });
});

describe("disarmLayoutDebug", () => {
  it("clears the persisted flag", () => {
    localStorage.setItem(LAYOUT_DEBUG_STORAGE_KEY, "1");

    disarmLayoutDebug();

    expect(localStorage.getItem(LAYOUT_DEBUG_STORAGE_KEY)).toBeNull();
    expect(isLayoutDebugRequested()).toBe(false);
  });

  it("also strips the query parameter, since the gate is an OR", () => {
    // Clearing only the flag would leave `?__layout-debug=1` mounting
    // the readout forever — the URL gate can add but never subtract.
    window.history.pushState({}, "", `/app?keep=yes&${LAYOUT_DEBUG_PARAM}=1#frag`);
    localStorage.setItem(LAYOUT_DEBUG_STORAGE_KEY, "1");

    disarmLayoutDebug();

    expect(isLayoutDebugParamRequested()).toBe(false);
    expect(isLayoutDebugRequested()).toBe(false);
    // Only the debug parameter goes — the rest of the URL is preserved,
    // so closing the readout doesn't disturb the page being measured.
    expect(window.location.pathname).toBe("/app");
    expect(window.location.search).toBe("?keep=yes");
    expect(window.location.hash).toBe("#frag");
  });

  it("tells React Router the URL moved, so no view can put it back", () => {
    // `replaceState` is invisible to the router, whose location snapshot
    // would keep the parameter. Views that build their next URL from
    // that snapshot — Artifacts' `openRow` does `new URLSearchParams(
    // params)` — would then re-add it on the next navigation and
    // remount the readout (code-reviewer, PR #505).
    const popped = vi.fn();
    window.addEventListener("popstate", popped);
    window.history.pushState({}, "", `/app?${LAYOUT_DEBUG_PARAM}=1`);

    disarmLayoutDebug();

    expect(popped).toHaveBeenCalledTimes(1);
    window.removeEventListener("popstate", popped);
  });

  it("stays quiet when there was no parameter to strip", () => {
    // Nothing moved, so nothing to announce — the persisted-flag path
    // must not churn the router on every Close in the PWA.
    const popped = vi.fn();
    window.addEventListener("popstate", popped);
    localStorage.setItem(LAYOUT_DEBUG_STORAGE_KEY, "1");

    disarmLayoutDebug();

    expect(popped).not.toHaveBeenCalled();
    window.removeEventListener("popstate", popped);
  });

  it("announces the change so the mount site re-renders", () => {
    const heard = vi.fn();
    window.addEventListener(LAYOUT_DEBUG_EVENT, heard);

    disarmLayoutDebug();

    expect(heard).toHaveBeenCalledTimes(1);
    window.removeEventListener(LAYOUT_DEBUG_EVENT, heard);
  });
});

describe("<LayoutDebug /> Close button", () => {
  it("unmounts the readout from inside — the only exit on a phone", () => {
    // `.layout-debug` is fixed/inset-0/z-index-9999 over an opaque
    // background, so the armed readout covers the brand mark and the
    // five-tap gesture can never undo itself. Without this control,
    // arming on a phone is a one-way trip (code-reviewer, PR #505).
    mountComposerFixture();
    setViewport({ windowHeight: 852, layoutHeight: 756 });
    localStorage.setItem(LAYOUT_DEBUG_STORAGE_KEY, "1");
    render(<GateHarness />);
    expect(screen.getByTestId("gate").textContent).toBe("true");

    render(<LayoutDebug />);
    act(() => {
      fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
    });

    expect(localStorage.getItem(LAYOUT_DEBUG_STORAGE_KEY)).toBeNull();
    expect(screen.getByTestId("gate").textContent).toBe("false");
  });
});
