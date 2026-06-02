// Copyright (c) 2026 John Carter. All rights reserved.
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api.js", () => ({
  listModels: vi.fn().mockResolvedValue({
    models: [
      { id: "claude-opus-4-6", label: "Claude Opus 4.6", tier: "Flagship" },
    ],
  }),
}));

import Composer from "./Composer.jsx";
import { __resetModelsCacheForTest } from "./data.js";
import {
  STORAGE_KEYS,
  __resetChannelPrefsForTest,
  __resetServerSyncForTest,
} from "../hooks/useChannelPrefs.js";

// Synthetic display-meta shape that mirrors what mergeWithDisplayMeta
// produces for the opus id. Used as the Composer prop in every test —
// the Composer doesn't fetch models itself; it just renders the prop.
const opus = {
  id: "claude-opus-4-6",
  name: "Claude Opus 4.6",
  short: "Opus 4.6",
  tier: "Flagship",
  desc: "Most capable",
};

function defaultProps(overrides = {}) {
  return {
    model: opus,
    effort: "High",
    setModel: vi.fn(),
    setEffort: vi.fn(),
    onSend: vi.fn(),
    placeholder: undefined,
    autofocus: false,
    ...overrides,
  };
}

describe("Composer", () => {
  let storage;

  beforeEach(() => {
    storage = {};
    vi.stubGlobal("localStorage", {
      getItem: (k) => storage[k] ?? null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; },
    });
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    __resetModelsCacheForTest();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    __resetModelsCacheForTest();
  });

  it("renders a textarea with the supplied placeholder", () => {
    render(<Composer {...defaultProps({ placeholder: "How can I help?" })} />);
    expect(screen.getByPlaceholderText("How can I help?")).toBeTruthy();
  });

  it("defaults the placeholder when none supplied", () => {
    render(<Composer {...defaultProps()} />);
    expect(screen.getByPlaceholderText(/how can i help/i)).toBeTruthy();
  });

  it("disables the send button when the input is empty", () => {
    render(<Composer {...defaultProps()} />);
    expect(screen.getByTitle("Send").disabled).toBe(true);
  });

  it("enables the send button once the user types text", () => {
    render(<Composer {...defaultProps()} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "hello" } });
    expect(screen.getByTitle("Send").disabled).toBe(false);
  });

  it("clicking Send invokes onSend with the typed text and clears the input", () => {
    const onSend = vi.fn();
    render(<Composer {...defaultProps({ onSend })} />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "hello world" } });
    fireEvent.click(screen.getByTitle("Send"));
    expect(onSend).toHaveBeenCalledWith("hello world", []);
    expect(ta.value).toBe("");
  });

  it("Enter (no shift) submits; Shift+Enter inserts a newline (default sendOnEnter=true)", () => {
    const onSend = vi.fn();
    render(<Composer {...defaultProps({ onSend })} />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "x" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledTimes(1);
    onSend.mockClear();
    fireEvent.change(ta, { target: { value: "x" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSend).toHaveBeenCalledTimes(0);
  });

  it("non-Enter keys are ignored by the keydown handler", () => {
    const onSend = vi.fn();
    render(<Composer {...defaultProps({ onSend })} />);
    const ta = screen.getByRole("textbox");
    fireEvent.change(ta, { target: { value: "x" } });
    fireEvent.keyDown(ta, { key: "a" });
    expect(onSend).not.toHaveBeenCalled();
  });

  describe("when sendOnEnter is false (newline-first mode)", () => {
    beforeEach(() => {
      storage[STORAGE_KEYS.sendOnEnter] = "0";
      __resetChannelPrefsForTest();
      __resetServerSyncForTest();
    });

    it("plain Enter does NOT submit (falls through to newline)", () => {
      const onSend = vi.fn();
      render(<Composer {...defaultProps({ onSend })} />);
      const ta = screen.getByRole("textbox");
      fireEvent.change(ta, { target: { value: "x" } });
      fireEvent.keyDown(ta, { key: "Enter", shiftKey: false });
      expect(onSend).not.toHaveBeenCalled();
    });

    it("Cmd+Enter (metaKey) submits", () => {
      const onSend = vi.fn();
      render(<Composer {...defaultProps({ onSend })} />);
      const ta = screen.getByRole("textbox");
      fireEvent.change(ta, { target: { value: "x" } });
      fireEvent.keyDown(ta, { key: "Enter", metaKey: true });
      expect(onSend).toHaveBeenCalledTimes(1);
    });

    it("Ctrl+Enter submits (non-Mac case)", () => {
      const onSend = vi.fn();
      render(<Composer {...defaultProps({ onSend })} />);
      const ta = screen.getByRole("textbox");
      fireEvent.change(ta, { target: { value: "x" } });
      fireEvent.keyDown(ta, { key: "Enter", ctrlKey: true });
      expect(onSend).toHaveBeenCalledTimes(1);
    });
  });

  it("renders the ModelPicker trigger with current model short name", () => {
    render(<Composer {...defaultProps()} />);
    expect(screen.getByText(opus.short)).toBeTruthy();
  });

  it("renders the mic and attach buttons", () => {
    render(<Composer {...defaultProps()} />);
    expect(screen.getByTitle("Add attachment")).toBeTruthy();
    expect(screen.getByTitle("Dictate")).toBeTruthy();
  });

  it("renders attachment chips when atts are added and a close button removes them", () => {
    render(<Composer {...defaultProps()} />);
    // Open attach menu, add a photo
    fireEvent.click(screen.getByTitle("Add attachment"));
    fireEvent.click(screen.getByText("Add photos or images"));
    expect(screen.getByText("diagram.png")).toBeTruthy();
    // Send button enabled by attachment alone
    expect(screen.getByTitle("Send").disabled).toBe(false);
  });

  it("removes attachment chip when close button is clicked", () => {
    render(<Composer {...defaultProps()} />);
    fireEvent.click(screen.getByTitle("Add attachment"));
    fireEvent.click(screen.getByText("Add photos or images"));
    expect(screen.getByText("diagram.png")).toBeTruthy();
    // Click the X to remove
    const closeBtn = screen.getByText("diagram.png").closest(".chip").querySelector(".x");
    fireEvent.click(closeBtn);
    expect(screen.queryByText("diagram.png")).toBeNull();
  });

  it("submits with the attaches-only fallback message when text is empty but atts exist", () => {
    const onSend = vi.fn();
    render(<Composer {...defaultProps({ onSend })} />);
    fireEvent.click(screen.getByTitle("Add attachment"));
    fireEvent.click(screen.getByText("Upload a file"));
    fireEvent.click(screen.getByTitle("Send"));
    expect(onSend).toHaveBeenCalledWith(expect.stringMatching(/take a look/i), expect.any(Array));
  });

  it("focuses the textarea when autofocus is true", () => {
    render(<Composer {...defaultProps({ autofocus: true })} />);
    expect(document.activeElement).toBe(screen.getByRole("textbox"));
  });

  it("does not invoke onSend when Enter is pressed with empty text and no attachments", () => {
    const onSend = vi.fn();
    render(<Composer {...defaultProps({ onSend })} />);
    const ta = screen.getByRole("textbox");
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: false });
    expect(onSend).not.toHaveBeenCalled();
  });
});
