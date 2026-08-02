// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LayoutDebug, {
  CHAIN_PROPS,
  LAYOUT_DEBUG_PARAM,
  SAFE_AREA_SIDES,
  collectChain,
  collectSnapshot,
  describeElement,
  elementLabel,
  findChainRoot,
  isLayoutDebugRequested,
  matchesDisplayMode,
  readSafeAreaInsets,
  round,
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
  window.history.pushState({}, "", "/");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

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
