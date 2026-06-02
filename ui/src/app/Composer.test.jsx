// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen } from "@testing-library/react";
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

// Factory for a fake SpeechRecognition class that records its
// instances so tests can drive the recognition lifecycle (onresult,
// onend, onerror, start, stop). Mirrors the Web Speech API surface
// the Composer touches.
function makeSpeechRecognitionStub() {
  const instances = [];
  function FakeSpeechRecognition() {
    const inst = {
      continuous: undefined,
      interimResults: undefined,
      onresult: null,
      onend: null,
      onerror: null,
      start: vi.fn(),
      stop: vi.fn(),
    };
    instances.push(inst);
    return inst;
  }
  return { ctor: FakeSpeechRecognition, instances };
}

// Helper that fires a synthetic `result` event on the most recently
// created recognition instance. Matches the SpeechRecognitionResultList
// shape the Composer iterates over. Wrapped in act() because firing
// the handler triggers a setText state update.
function fireResult(instance, transcript) {
  act(function dispatchResult() {
    instance.onresult({
      results: [[{ transcript }]],
    });
  });
}

describe("Composer", () => {
  let storage;
  let speech;

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
    speech = makeSpeechRecognitionStub();
    // Default: feature is available so the existing mic-button tests
    // keep finding the control. Individual tests below clear the stub
    // to exercise the missing-API path.
    window.SpeechRecognition = speech.ctor;
    __resetChannelPrefsForTest();
    __resetServerSyncForTest();
    __resetModelsCacheForTest();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.SpeechRecognition;
    delete window.webkitSpeechRecognition;
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

  describe("dictation (Web Speech API)", () => {
    it("hides the mic button when neither SpeechRecognition nor webkitSpeechRecognition is available", () => {
      delete window.SpeechRecognition;
      delete window.webkitSpeechRecognition;
      render(<Composer {...defaultProps()} />);
      expect(screen.queryByTitle("Dictate")).toBeNull();
    });

    it("falls back to webkitSpeechRecognition when SpeechRecognition is unavailable", () => {
      delete window.SpeechRecognition;
      window.webkitSpeechRecognition = speech.ctor;
      render(<Composer {...defaultProps()} />);
      expect(screen.getByTitle("Dictate")).toBeTruthy();
    });

    it("clicking the mic starts a recognition session with the documented config", () => {
      render(<Composer {...defaultProps()} />);
      fireEvent.click(screen.getByTitle("Dictate"));
      expect(speech.instances).toHaveLength(1);
      const inst = speech.instances[0];
      expect(inst.continuous).toBe(false);
      expect(inst.interimResults).toBe(true);
      expect(inst.start).toHaveBeenCalledTimes(1);
    });

    it("re-clicking the mic stops the active session", () => {
      render(<Composer {...defaultProps()} />);
      fireEvent.click(screen.getByTitle("Dictate"));
      fireEvent.click(screen.getByTitle("Stop dictation"));
      expect(speech.instances[0].stop).toHaveBeenCalledTimes(1);
    });

    it("renders an aria-pressed=true + 'Stop dictation' affordance while active", () => {
      render(<Composer {...defaultProps()} />);
      fireEvent.click(screen.getByTitle("Dictate"));
      const btn = screen.getByTitle("Stop dictation");
      expect(btn.getAttribute("aria-pressed")).toBe("true");
      expect(btn.className).toContain("on");
    });

    it("onresult event appends the transcript to the textarea value", () => {
      render(<Composer {...defaultProps()} />);
      fireEvent.click(screen.getByTitle("Dictate"));
      fireResult(speech.instances[0], "hello world");
      expect(screen.getByRole("textbox").value).toBe("hello world");
    });

    it("preserves the caret position the user had when they tapped mic", () => {
      render(<Composer {...defaultProps()} />);
      const ta = screen.getByRole("textbox");
      // Type "AB" then move caret to index 1 (between A and B).
      fireEvent.change(ta, { target: { value: "AB" } });
      ta.setSelectionRange(1, 1);
      fireEvent.click(screen.getByTitle("Dictate"));
      fireResult(speech.instances[0], "X");
      // Transcript splices in at caret: A + X + B.
      expect(ta.value).toBe("AXB");
    });

    it("falls back to end-of-text when the textarea reports null selectionStart", () => {
      render(<Composer {...defaultProps()} />);
      const ta = screen.getByRole("textbox");
      fireEvent.change(ta, { target: { value: "HEAD" } });
      // Simulate browsers where selectionStart is null until focus —
      // exercises the `?? ta.value.length` fallback.
      Object.defineProperty(ta, "selectionStart", { value: null, configurable: true });
      fireEvent.click(screen.getByTitle("Dictate"));
      fireResult(speech.instances[0], "-TAIL");
      // With caret defaulted to value.length (4), transcript appends at end.
      expect(ta.value).toBe("HEAD-TAIL");
    });

    it("onend handler reverts the button label back to 'Dictate'", () => {
      render(<Composer {...defaultProps()} />);
      fireEvent.click(screen.getByTitle("Dictate"));
      expect(screen.getByTitle("Stop dictation")).toBeTruthy();
      act(() => speech.instances[0].onend());
      expect(screen.getByTitle("Dictate")).toBeTruthy();
    });

    it("onerror handler reverts the button label back to 'Dictate'", () => {
      render(<Composer {...defaultProps()} />);
      fireEvent.click(screen.getByTitle("Dictate"));
      act(() => speech.instances[0].onerror({ error: "no-speech" }));
      expect(screen.getByTitle("Dictate")).toBeTruthy();
    });

    it("submitting stops any active recognition", () => {
      const onSend = vi.fn();
      render(<Composer {...defaultProps({ onSend })} />);
      fireEvent.change(screen.getByRole("textbox"), { target: { value: "hi" } });
      fireEvent.click(screen.getByTitle("Dictate"));
      fireEvent.click(screen.getByTitle("Send"));
      expect(speech.instances[0].stop).toHaveBeenCalled();
      expect(onSend).toHaveBeenCalledWith("hi", []);
    });

    it("unmount stops the active recognition", () => {
      const { unmount } = render(<Composer {...defaultProps()} />);
      fireEvent.click(screen.getByTitle("Dictate"));
      const inst = speech.instances[0];
      unmount();
      expect(inst.stop).toHaveBeenCalled();
    });

    it("recognition constructor throwing keeps the button in idle state", () => {
      window.SpeechRecognition = function ThrowingCtor() {
        throw new Error("permission denied");
      };
      render(<Composer {...defaultProps()} />);
      expect(() => fireEvent.click(screen.getByTitle("Dictate"))).not.toThrow();
      // Still "Dictate" — no active session.
      expect(screen.getByTitle("Dictate")).toBeTruthy();
    });

    it("start() throwing collapses back to idle", () => {
      const stub = makeSpeechRecognitionStub();
      stub.ctor = function CtorThatBuildsThrowingInstance() {
        const inst = {
          continuous: undefined,
          interimResults: undefined,
          onresult: null,
          onend: null,
          onerror: null,
          start: vi.fn(() => { throw new Error("already started"); }),
          stop: vi.fn(),
        };
        stub.instances.push(inst);
        return inst;
      };
      window.SpeechRecognition = stub.ctor;
      render(<Composer {...defaultProps()} />);
      fireEvent.click(screen.getByTitle("Dictate"));
      expect(screen.getByTitle("Dictate")).toBeTruthy();
      expect(stub.instances[0].start).toHaveBeenCalled();
    });

    it("stop() throwing during unmount is swallowed silently", () => {
      const stub = makeSpeechRecognitionStub();
      stub.ctor = function CtorThatBuildsThrowingStop() {
        const inst = {
          continuous: undefined,
          interimResults: undefined,
          onresult: null,
          onend: null,
          onerror: null,
          start: vi.fn(),
          stop: vi.fn(() => { throw new Error("not started"); }),
        };
        stub.instances.push(inst);
        return inst;
      };
      window.SpeechRecognition = stub.ctor;
      const { unmount } = render(<Composer {...defaultProps()} />);
      fireEvent.click(screen.getByTitle("Dictate"));
      expect(() => unmount()).not.toThrow();
    });

    it("stop() throwing during the toggle re-click is swallowed silently", () => {
      const stub = makeSpeechRecognitionStub();
      stub.ctor = function CtorThatBuildsThrowingStop() {
        const inst = {
          continuous: undefined,
          interimResults: undefined,
          onresult: null,
          onend: null,
          onerror: null,
          start: vi.fn(),
          stop: vi.fn(() => { throw new Error("not started"); }),
        };
        stub.instances.push(inst);
        return inst;
      };
      window.SpeechRecognition = stub.ctor;
      render(<Composer {...defaultProps()} />);
      fireEvent.click(screen.getByTitle("Dictate"));
      expect(() => fireEvent.click(screen.getByTitle("Stop dictation"))).not.toThrow();
    });
  });
});
