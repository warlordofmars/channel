// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api.js", () => ({
  listModels: vi.fn().mockResolvedValue({
    models: [
      { id: "claude-opus-4-6", label: "Claude Opus 4.6", tier: "Flagship" },
    ],
  }),
  // #177 attach pipeline — mocked per-test where outcomes matter.
  // Default to a happy-path that resolves to a deterministic Attachment so
  // tests not exercising the pipeline don't need to stub it inline.
  sha256Hex: vi.fn().mockResolvedValue("sha-stub"),
  presignAttachment: vi.fn().mockResolvedValue({
    att_id: "att-mock",
    url: "https://s3.example/u",
    required_headers: { "Content-Type": "application/pdf" },
    presign_token: "jwt.mock",
  }),
  uploadToPresigned: vi.fn().mockResolvedValue(undefined),
  finalizeAttachment: vi.fn().mockResolvedValue({
    id: "att-mock",
    user_id: "u-1",
    name: "spec.pdf",
    mime: "application/pdf",
    size_bytes: 1024,
    s3_key: "k",
    s3_bucket: "bk",
    checksum_sha256: "sha-stub",
    created_at: "2026-06-05T00:00:00Z",
  }),
}));

import Composer, {
  formatAttachmentSize,
  iconForMime,
  truncateName,
} from "./Composer.jsx";
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

// Shared attachment-pipeline helpers — used by the #177 attachments
// describe block and the #178 chip-polish describe block. Re-pulling
// the mocked api fns through dynamic import keeps each test's reset
// scope correct, and `pickFiles` drives the hidden file input that
// every chip-render test eventually leans on.
async function getApiMocks() {
  const mod = await import("../api.js");
  return mod;
}

function makeFile(name, type, size = 1024) {
  const blob = new Blob([new Uint8Array(size)], { type });
  return new File([blob], name, { type });
}

async function pickFiles(files) {
  const input = screen.getByTestId("attach-file-input");
  await act(async () => {
    fireEvent.change(input, { target: { files } });
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
    expect(screen.getByTitle("Attach files")).toBeTruthy();
    expect(screen.getByTitle("Dictate")).toBeTruthy();
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

  // ----------------------------------------------------------------
  // Attachment pipeline (#177)
  // ----------------------------------------------------------------
  describe("attachments (#177)", () => {
    it("rejects an oversized file with an inline error and skips the pipeline", async () => {
      const api = await getApiMocks();
      api.presignAttachment.mockClear();
      render(<Composer {...defaultProps()} />);
      const big = makeFile("big.pdf", "application/pdf", 20 * 1024 * 1024 + 1);
      await pickFiles([big]);
      expect(screen.getByRole("alert").textContent).toMatch(/too large/i);
      expect(api.presignAttachment).not.toHaveBeenCalled();
    });

    it("rejects a disallowed MIME with an inline error", async () => {
      const api = await getApiMocks();
      api.presignAttachment.mockClear();
      render(<Composer {...defaultProps()} />);
      await pickFiles([makeFile("evil.exe", "application/x-msdownload", 1)]);
      expect(screen.getByRole("alert").textContent).toMatch(/file type not supported/i);
      expect(api.presignAttachment).not.toHaveBeenCalled();
    });

    it("rejects the 6th file in a single message", async () => {
      const api = await getApiMocks();
      api.presignAttachment.mockClear();
      render(<Composer {...defaultProps()} />);
      const six = Array.from({ length: 6 }, (_, i) =>
        makeFile(`f${i}.pdf`, "application/pdf", 1),
      );
      await pickFiles(six);
      expect(screen.getByRole("alert").textContent).toMatch(/more than 5/i);
      expect(api.presignAttachment).not.toHaveBeenCalled();
    });

    it("happy-path PDF: pending chip flips to attached and the id lands in send", async () => {
      const api = await getApiMocks();
      api.finalizeAttachment.mockResolvedValueOnce({
        id: "att-pdf-1",
        user_id: "u",
        name: "spec.pdf",
        mime: "application/pdf",
        size_bytes: 1024,
        s3_key: "k",
        s3_bucket: "bk",
        checksum_sha256: "sha",
        created_at: "t",
      });
      const onSend = vi.fn();
      render(<Composer {...defaultProps({ onSend })} />);
      await pickFiles([makeFile("spec.pdf", "application/pdf", 1024)]);
      // Pending chip immediately.
      expect(screen.getByText("spec.pdf")).toBeTruthy();
      // After the pipeline resolves, the chip's status flips.
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      const chip = screen.getByText("spec.pdf").closest(".attach-chip");
      expect(chip.getAttribute("data-status")).toBe("attached");
      // Send button is enabled now.
      expect(screen.getByTitle("Send").disabled).toBe(false);
      fireEvent.click(screen.getByTitle("Send"));
      // onSend receives the att id, not the full Attachment.
      expect(onSend).toHaveBeenCalledWith(
        expect.any(String),
        [{ id: "att-pdf-1" }],
      );
    });

    it("happy-path image: pending chip flips to attached", async () => {
      const api = await getApiMocks();
      api.finalizeAttachment.mockResolvedValueOnce({
        id: "att-img-1",
        user_id: "u",
        name: "screenshot.png",
        mime: "image/png",
        size_bytes: 2048,
        s3_key: "k",
        s3_bucket: "bk",
        checksum_sha256: "sha",
        created_at: "t",
      });
      render(<Composer {...defaultProps()} />);
      await pickFiles([makeFile("screenshot.png", "image/png", 2048)]);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      const chip = screen.getByText("screenshot.png").closest(".attach-chip");
      expect(chip.getAttribute("data-status")).toBe("attached");
    });

    it("send button is disabled while a chip is still attaching", async () => {
      const api = await getApiMocks();
      // Block the pipeline mid-flight so the chip stays in 'attaching' state.
      let resolveFinalize;
      api.finalizeAttachment.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFinalize = resolve;
          }),
      );
      render(<Composer {...defaultProps()} />);
      await pickFiles([makeFile("slow.pdf", "application/pdf", 1)]);
      const chip = screen.getByText("slow.pdf").closest(".attach-chip");
      expect(chip.getAttribute("data-status")).toBe("attaching");
      expect(screen.getByTitle("Waiting for attachments…").disabled).toBe(true);
      // Unblock so afterEach doesn't dangle.
      await act(async () => {
        resolveFinalize({
          id: "x",
          user_id: "u",
          name: "slow.pdf",
          mime: "application/pdf",
          size_bytes: 1,
          s3_key: "k",
          s3_bucket: "bk",
          checksum_sha256: "sha",
          created_at: "t",
        });
        await new Promise((r) => setTimeout(r, 0));
      });
    });

    it("failed chip exposes a retry that re-runs the pipeline", async () => {
      const api = await getApiMocks();
      api.finalizeAttachment.mockRejectedValueOnce(new Error("boom"));
      render(<Composer {...defaultProps()} />);
      await pickFiles([makeFile("retry.pdf", "application/pdf", 1)]);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      const chip = screen.getByText("retry.pdf").closest(".attach-chip");
      expect(chip.getAttribute("data-status")).toBe("failed");
      // Retry path uses the next mock outcome — happy.
      api.finalizeAttachment.mockResolvedValueOnce({
        id: "att-retry-1",
        user_id: "u",
        name: "retry.pdf",
        mime: "application/pdf",
        size_bytes: 1,
        s3_key: "k",
        s3_bucket: "bk",
        checksum_sha256: "sha",
        created_at: "t",
      });
      await act(async () => {
        fireEvent.click(screen.getByTitle("Retry"));
      });
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      const after = screen.getByText("retry.pdf").closest(".attach-chip");
      expect(after.getAttribute("data-status")).toBe("attached");
    });

    it("clicking × on a pending chip removes it from the composer", async () => {
      render(<Composer {...defaultProps()} />);
      await pickFiles([makeFile("doomed.pdf", "application/pdf", 1)]);
      const removeBtn = screen
        .getByText("doomed.pdf")
        .closest(".attach-chip")
        .querySelector(".x");
      fireEvent.click(removeBtn);
      expect(screen.queryByText("doomed.pdf")).toBeNull();
    });

    it("drag-and-drop routes through the same pipeline as the picker", async () => {
      const api = await getApiMocks();
      api.finalizeAttachment.mockResolvedValueOnce({
        id: "att-drop-1",
        user_id: "u",
        name: "dropped.pdf",
        mime: "application/pdf",
        size_bytes: 1,
        s3_key: "k",
        s3_bucket: "bk",
        checksum_sha256: "sha",
        created_at: "t",
      });
      const { container } = render(<Composer {...defaultProps()} />);
      const wrap = container.querySelector(".composer-wrap");
      const file = makeFile("dropped.pdf", "application/pdf", 1);
      const dataTransfer = { files: [file], types: ["Files"] };
      fireEvent.dragEnter(wrap, { dataTransfer });
      expect(container.querySelector(".drop-overlay")).toBeTruthy();
      await act(async () => {
        fireEvent.drop(wrap, { dataTransfer });
      });
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      expect(container.querySelector(".drop-overlay")).toBeFalsy();
      const chip = screen.getByText("dropped.pdf").closest(".attach-chip");
      expect(chip.getAttribute("data-status")).toBe("attached");
    });

    it("drag of non-file items is ignored (no overlay)", async () => {
      const { container } = render(<Composer {...defaultProps()} />);
      const wrap = container.querySelector(".composer-wrap");
      fireEvent.dragEnter(wrap, { dataTransfer: { types: ["text/plain"] } });
      expect(container.querySelector(".drop-overlay")).toBeFalsy();
    });

    it("dragleave hides the overlay once the counter drains to zero", async () => {
      const { container } = render(<Composer {...defaultProps()} />);
      const wrap = container.querySelector(".composer-wrap");
      const dt = { types: ["Files"] };
      fireEvent.dragEnter(wrap, { dataTransfer: dt });
      fireEvent.dragEnter(wrap, { dataTransfer: dt });
      expect(container.querySelector(".drop-overlay")).toBeTruthy();
      fireEvent.dragLeave(wrap);
      expect(container.querySelector(".drop-overlay")).toBeTruthy();
      fireEvent.dragLeave(wrap);
      expect(container.querySelector(".drop-overlay")).toBeFalsy();
    });

    it("dragover on a file drag calls preventDefault so drop fires", async () => {
      const { container } = render(<Composer {...defaultProps()} />);
      const wrap = container.querySelector(".composer-wrap");
      // fireEvent returns false when the event's default action was
      // prevented (React's synthetic preventDefault forwards to the
      // underlying native event).
      const propagated = fireEvent.dragOver(wrap, {
        dataTransfer: { types: ["Files"] },
      });
      expect(propagated).toBe(false);
    });

    it("non-file dragover does NOT preventDefault", async () => {
      const { container } = render(<Composer {...defaultProps()} />);
      const wrap = container.querySelector(".composer-wrap");
      const propagated = fireEvent.dragOver(wrap, {
        dataTransfer: { types: ["text/plain"] },
      });
      expect(propagated).toBe(true);
    });

    it("uses 'attach' terminology — never 'upload'", async () => {
      render(<Composer {...defaultProps()} />);
      const allText = document.body.textContent ?? "";
      expect(allText.toLowerCase()).not.toContain("upload");
    });

    it("submit while a chip is attaching is a no-op", async () => {
      const api = await getApiMocks();
      let resolveFinalize;
      api.finalizeAttachment.mockImplementationOnce(
        () => new Promise((resolve) => { resolveFinalize = resolve; }),
      );
      const onSend = vi.fn();
      render(<Composer {...defaultProps({ onSend })} />);
      await pickFiles([makeFile("blocking.pdf", "application/pdf", 1)]);
      // Type text — should be enabled now? No — anyAttaching blocks.
      const ta = screen.getByRole("textbox");
      fireEvent.change(ta, { target: { value: "go" } });
      fireEvent.keyDown(ta, { key: "Enter" });
      expect(onSend).not.toHaveBeenCalled();
      // Drain the pending promise.
      await act(async () => {
        resolveFinalize({
          id: "x",
          user_id: "u",
          name: "blocking.pdf",
          mime: "application/pdf",
          size_bytes: 1,
          s3_key: "k",
          s3_bucket: "bk",
          checksum_sha256: "sha",
          created_at: "t",
        });
        await new Promise((r) => setTimeout(r, 0));
      });
    });

    it("submit with empty text + zero attached chips is a no-op", () => {
      const onSend = vi.fn();
      render(<Composer {...defaultProps({ onSend })} />);
      fireEvent.click(screen.getByTitle("Send"));
      expect(onSend).not.toHaveBeenCalled();
    });

    it("submit with text + attached chip emits both", async () => {
      const api = await getApiMocks();
      api.finalizeAttachment.mockResolvedValueOnce({
        id: "att-both-1",
        user_id: "u",
        name: "both.pdf",
        mime: "application/pdf",
        size_bytes: 1,
        s3_key: "k",
        s3_bucket: "bk",
        checksum_sha256: "sha",
        created_at: "t",
      });
      const onSend = vi.fn();
      render(<Composer {...defaultProps({ onSend })} />);
      await pickFiles([makeFile("both.pdf", "application/pdf", 1)]);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      const ta = screen.getByRole("textbox");
      fireEvent.change(ta, { target: { value: "look at this" } });
      fireEvent.click(screen.getByTitle("Send"));
      expect(onSend).toHaveBeenCalledWith("look at this", [{ id: "att-both-1" }]);
    });

    it("submit with empty text + attached chip uses the fallback message", async () => {
      const api = await getApiMocks();
      api.finalizeAttachment.mockResolvedValueOnce({
        id: "att-fb-1",
        user_id: "u",
        name: "fb.pdf",
        mime: "application/pdf",
        size_bytes: 1,
        s3_key: "k",
        s3_bucket: "bk",
        checksum_sha256: "sha",
        created_at: "t",
      });
      const onSend = vi.fn();
      render(<Composer {...defaultProps({ onSend })} />);
      await pickFiles([makeFile("fb.pdf", "application/pdf", 1)]);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      fireEvent.click(screen.getByTitle("Send"));
      expect(onSend).toHaveBeenCalledWith(
        expect.stringMatching(/take a look/i),
        [{ id: "att-fb-1" }],
      );
    });

    it("after a successful send, the chip list is cleared", async () => {
      const api = await getApiMocks();
      api.finalizeAttachment.mockResolvedValueOnce({
        id: "att-clr-1",
        user_id: "u",
        name: "clr.pdf",
        mime: "application/pdf",
        size_bytes: 1,
        s3_key: "k",
        s3_bucket: "bk",
        checksum_sha256: "sha",
        created_at: "t",
      });
      render(<Composer {...defaultProps()} />);
      await pickFiles([makeFile("clr.pdf", "application/pdf", 1)]);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      fireEvent.click(screen.getByTitle("Send"));
      expect(screen.queryByText("clr.pdf")).toBeNull();
    });

    it("picking zero files (empty FileList) is a no-op", async () => {
      const api = await getApiMocks();
      api.presignAttachment.mockClear();
      render(<Composer {...defaultProps()} />);
      await pickFiles([]);
      expect(api.presignAttachment).not.toHaveBeenCalled();
    });

    it("two concurrent uploads resolve independently — sibling chip stays pending while the other flips", async () => {
      const api = await getApiMocks();
      // First finalize stalls, second resolves immediately.
      let resolveSlow;
      api.finalizeAttachment
        .mockImplementationOnce(() => new Promise((r) => { resolveSlow = r; }))
        .mockResolvedValueOnce({
          id: "att-fast",
          user_id: "u",
          name: "fast.pdf",
          mime: "application/pdf",
          size_bytes: 1,
          s3_key: "k",
          s3_bucket: "bk",
          checksum_sha256: "sha",
          created_at: "t",
        });
      render(<Composer {...defaultProps()} />);
      await pickFiles([
        makeFile("slow.pdf", "application/pdf", 1),
        makeFile("fast.pdf", "application/pdf", 1),
      ]);
      // Wait for the fast pipeline to resolve.
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      const slowChip = screen.getByText("slow.pdf").closest(".attach-chip");
      const fastChip = screen.getByText("fast.pdf").closest(".attach-chip");
      expect(slowChip.getAttribute("data-status")).toBe("attaching");
      expect(fastChip.getAttribute("data-status")).toBe("attached");
      // Drain so the test cleanly exits.
      await act(async () => {
        resolveSlow({
          id: "att-slow",
          user_id: "u",
          name: "slow.pdf",
          mime: "application/pdf",
          size_bytes: 1,
          s3_key: "k",
          s3_bucket: "bk",
          checksum_sha256: "sha",
          created_at: "t",
        });
        await new Promise((r) => setTimeout(r, 0));
      });
    });

    it("drop with no dataTransfer is a no-op (defensive — synthetic events occasionally omit it)", async () => {
      const api = await getApiMocks();
      api.presignAttachment.mockClear();
      const { container } = render(<Composer {...defaultProps()} />);
      const wrap = container.querySelector(".composer-wrap");
      await act(async () => {
        fireEvent.drop(wrap, { dataTransfer: null });
      });
      expect(api.presignAttachment).not.toHaveBeenCalled();
    });

    it("dragenter with no dataTransfer.types does not show the overlay", async () => {
      const { container } = render(<Composer {...defaultProps()} />);
      const wrap = container.querySelector(".composer-wrap");
      fireEvent.dragEnter(wrap, { dataTransfer: {} });
      expect(container.querySelector(".drop-overlay")).toBeFalsy();
    });

    it("dragover with no dataTransfer.types does not preventDefault", async () => {
      const { container } = render(<Composer {...defaultProps()} />);
      const wrap = container.querySelector(".composer-wrap");
      const propagated = fireEvent.dragOver(wrap, { dataTransfer: {} });
      expect(propagated).toBe(true);
    });

    it("removing one of two chips leaves the other intact (filter false branch)", async () => {
      const api = await getApiMocks();
      // Stall both so they stay pending while we remove one.
      let resolveA;
      let resolveB;
      api.finalizeAttachment
        .mockImplementationOnce(() => new Promise((r) => { resolveA = r; }))
        .mockImplementationOnce(() => new Promise((r) => { resolveB = r; }));
      render(<Composer {...defaultProps()} />);
      await pickFiles([
        makeFile("a.pdf", "application/pdf", 1),
        makeFile("b.pdf", "application/pdf", 1),
      ]);
      const aRemove = screen
        .getByText("a.pdf")
        .closest(".attach-chip")
        .querySelector(".x");
      fireEvent.click(aRemove);
      expect(screen.queryByText("a.pdf")).toBeNull();
      expect(screen.getByText("b.pdf")).toBeTruthy();
      // Drain so the test cleanly exits.
      await act(async () => {
        resolveA?.({ id: "x", user_id: "u", name: "a", mime: "application/pdf", size_bytes: 1, s3_key: "k", s3_bucket: "bk", checksum_sha256: "sha", created_at: "t" });
        resolveB({ id: "y", user_id: "u", name: "b", mime: "application/pdf", size_bytes: 1, s3_key: "k", s3_bucket: "bk", checksum_sha256: "sha", created_at: "t" });
        await new Promise((r) => setTimeout(r, 0));
      });
    });

    it("retry on a failed chip only changes that chip's status (map false branch)", async () => {
      const api = await getApiMocks();
      // First chip succeeds; second fails.
      api.finalizeAttachment
        .mockResolvedValueOnce({
          id: "ok",
          user_id: "u",
          name: "ok.pdf",
          mime: "application/pdf",
          size_bytes: 1,
          s3_key: "k",
          s3_bucket: "bk",
          checksum_sha256: "sha",
          created_at: "t",
        })
        .mockRejectedValueOnce(new Error("boom"));
      render(<Composer {...defaultProps()} />);
      await pickFiles([
        makeFile("ok.pdf", "application/pdf", 1),
        makeFile("fail.pdf", "application/pdf", 1),
      ]);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      // Retry the failed chip; happy outcome on the retry.
      api.finalizeAttachment.mockResolvedValueOnce({
        id: "ok2",
        user_id: "u",
        name: "fail.pdf",
        mime: "application/pdf",
        size_bytes: 1,
        s3_key: "k",
        s3_bucket: "bk",
        checksum_sha256: "sha",
        created_at: "t",
      });
      await act(async () => {
        fireEvent.click(screen.getByTitle("Retry"));
      });
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      // Both chips should now read "attached", and the first one must
      // still carry its original id (the retry's map shouldn't have
      // touched it).
      const okChip = screen.getByText("ok.pdf").closest(".attach-chip");
      const fixChip = screen.getByText("fail.pdf").closest(".attach-chip");
      expect(okChip.getAttribute("data-status")).toBe("attached");
      expect(fixChip.getAttribute("data-status")).toBe("attached");
    });

    it("non-Error exception in the pipeline uses the generic 'Failed to attach' reason", async () => {
      const api = await getApiMocks();
      // Throw a non-Error value (e.g. a plain string) so the
      // ``err instanceof Error`` branch falls to the default reason.
      api.finalizeAttachment.mockRejectedValueOnce("not an Error");
      render(<Composer {...defaultProps()} />);
      await pickFiles([makeFile("bad.pdf", "application/pdf", 1)]);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      const chip = screen.getByText("bad.pdf").closest(".attach-chip");
      expect(chip.getAttribute("data-status")).toBe("failed");
    });
  });

  describe("chip polish (#178)", () => {
    describe("iconForMime", () => {
      it.each([
        ["application/pdf", "file-pdf"],
        ["image/png", "file-image"],
        ["image/jpeg", "file-image"],
        ["image/gif", "file-image"],
        ["image/webp", "file-image"],
        ["text/csv", "file-sheet"],
        [
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "file-sheet",
        ],
        ["text/plain", "file-text"],
        ["text/markdown", "file-text"],
      ])("maps %s to '%s'", (mime, expected) => {
        expect(iconForMime(mime)).toBe(expected);
      });

      it("falls back to the generic 'file' icon for unknown MIMEs", () => {
        expect(iconForMime("application/octet-stream")).toBe("file");
        expect(iconForMime(undefined)).toBe("file");
      });
    });

    describe("formatAttachmentSize", () => {
      it.each([
        [0, "1 KB"],
        [512, "1 KB"],
        [1024, "1 KB"],
        [204_800, "200 KB"],
        [1024 * 1024, "1.0 MB"],
        [5_242_880, "5.0 MB"],
        [20 * 1024 * 1024, "20.0 MB"],
      ])("formats %d bytes as '%s'", (bytes, expected) => {
        expect(formatAttachmentSize(bytes)).toBe(expected);
      });
    });

    describe("truncateName", () => {
      it("returns the name unchanged when ≤20 chars", () => {
        expect(truncateName("spec.pdf")).toBe("spec.pdf");
        expect(truncateName("just-on-the-edge.pdf")).toBe("just-on-the-edge.pdf");
      });

      it("appends an ellipsis when the name exceeds 20 chars", () => {
        // max=20: slice(0, 19) + "…" → 19-char prefix + ellipsis = 20 chars.
        expect(truncateName("very-long-filename-with-extra-tail.pdf")).toBe(
          "very-long-filename-…",
        );
      });
    });

    it("renders the MIME-typed icon inside each pending chip", async () => {
      const api = await getApiMocks();
      api.finalizeAttachment.mockResolvedValueOnce({
        id: "att-pdf-1",
        user_id: "u",
        name: "spec.pdf",
        mime: "application/pdf",
        size_bytes: 1024,
        s3_key: "k",
        s3_bucket: "bk",
        checksum_sha256: "sha",
        created_at: "t",
      });
      render(<Composer {...defaultProps()} />);
      await pickFiles([makeFile("spec.pdf", "application/pdf", 1024)]);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      const chip = screen.getByText("spec.pdf").closest(".attach-chip");
      // The chip's tile carries the MIME-typed icon. SVG glyph identity
      // isn't directly inspectable on the rendered element, so we
      // assert via the helper's contract (covered above) and the
      // presence of an svg inside the tile.
      expect(chip.querySelector(".tile svg")).toBeTruthy();
    });

    it("shows the attachment size next to the filename", async () => {
      const api = await getApiMocks();
      api.finalizeAttachment.mockResolvedValueOnce({
        id: "att-png-1",
        user_id: "u",
        name: "shot.png",
        mime: "image/png",
        size_bytes: 524_288,
        s3_key: "k",
        s3_bucket: "bk",
        checksum_sha256: "sha",
        created_at: "t",
      });
      render(<Composer {...defaultProps()} />);
      await pickFiles([makeFile("shot.png", "image/png", 524_288)]);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
      const chip = screen.getByText("shot.png").closest(".attach-chip");
      expect(chip.querySelector(".sz").textContent).toBe("512 KB");
    });

    it("renders the MCPPicker pill when mcpServers prop is non-null (#207)", () => {
      render(
        <Composer
          {...defaultProps({
            mcpServers: [],
            mcpSettings: { mode: "inherit", explicit_server_ids: [] },
            setMcpSettings: vi.fn(),
          })}
        />,
      );
      expect(
        screen.getByRole("button", { name: /0 tool servers/i }),
      ).toBeInTheDocument();
    });

    it("does NOT render the MCPPicker pill when mcpServers prop is null", () => {
      render(<Composer {...defaultProps()} />);
      expect(
        screen.queryByRole("button", { name: /tool servers?\b/i }),
      ).toBeNull();
    });

    it("exposes the full filename via title= on the chip", async () => {
      render(<Composer {...defaultProps()} />);
      const longName = "very-long-attachment-filename.pdf";
      await pickFiles([makeFile(longName, "application/pdf", 1024)]);
      const chip = screen.getByTitle(longName);
      expect(chip.classList.contains("attach-chip")).toBe(true);
      // The visible label is truncated but the title carries the full
      // name so hover reveals it.
      expect(chip.querySelector(".nm").textContent).not.toBe(longName);
      expect(chip.querySelector(".nm").textContent.endsWith("…")).toBe(true);
      // Cleanup: settle the in-flight pipeline so afterEach has nothing
      // pending.
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    });
  });

  // #211: the send path resolves { accepted: false } when the POST was
  // refused before streaming (422 too-long, 413, auth, network). The
  // Composer restores the cleared input so the user's paste isn't lost.
  describe("#211 — restore input when the send is refused", () => {
    // Settle a picked file's attach pipeline (all api mocks resolve).
    async function settlePipeline() {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }

    it("restores the text when onSend resolves { accepted: false }", async () => {
      const onSend = vi.fn().mockResolvedValue({ accepted: false });
      render(<Composer {...defaultProps({ onSend })} />);
      const ta = screen.getByRole("textbox");
      fireEvent.change(ta, { target: { value: "way too long paste" } });
      await act(async () => {
        fireEvent.click(screen.getByTitle("Send"));
      });
      expect(onSend).toHaveBeenCalledWith("way too long paste", []);
      expect(ta.value).toBe("way too long paste");
    });

    it("does not clobber text the user typed while the refusal was in flight", async () => {
      let resolveSend;
      const onSend = vi.fn(
        () =>
          new Promise((resolve) => {
            resolveSend = resolve;
          }),
      );
      render(<Composer {...defaultProps({ onSend })} />);
      const ta = screen.getByRole("textbox");
      fireEvent.change(ta, { target: { value: "first paste" } });
      fireEvent.click(screen.getByTitle("Send"));
      // Cleared optimistically…
      expect(ta.value).toBe("");
      // …user starts a new draft before the refusal lands.
      fireEvent.change(ta, { target: { value: "second draft" } });
      await act(async () => {
        resolveSend({ accepted: false });
      });
      expect(ta.value).toBe("second draft");
    });

    it("leaves the input cleared when onSend resolves { accepted: true }", async () => {
      const onSend = vi.fn().mockResolvedValue({ accepted: true });
      render(<Composer {...defaultProps({ onSend })} />);
      const ta = screen.getByRole("textbox");
      fireEvent.change(ta, { target: { value: "hello" } });
      await act(async () => {
        fireEvent.click(screen.getByTitle("Send"));
      });
      expect(ta.value).toBe("");
    });

    it("leaves the input cleared on a user-initiated abort", async () => {
      const onSend = vi
        .fn()
        .mockResolvedValue({ accepted: false, aborted: true });
      render(<Composer {...defaultProps({ onSend })} />);
      const ta = screen.getByRole("textbox");
      fireEvent.change(ta, { target: { value: "hello" } });
      await act(async () => {
        fireEvent.click(screen.getByTitle("Send"));
      });
      expect(ta.value).toBe("");
    });

    it("restores the input when onSend rejects (e.g. chat creation failed)", async () => {
      const onSend = vi.fn().mockRejectedValue(new Error("createChat 500"));
      render(<Composer {...defaultProps({ onSend })} />);
      const ta = screen.getByRole("textbox");
      fireEvent.change(ta, { target: { value: "hello" } });
      await act(async () => {
        fireEvent.click(screen.getByTitle("Send"));
      });
      expect(ta.value).toBe("hello");
    });

    it("restores the input when onSend throws synchronously", async () => {
      const onSend = vi.fn(() => {
        throw new Error("sync boom");
      });
      render(<Composer {...defaultProps({ onSend })} />);
      const ta = screen.getByRole("textbox");
      fireEvent.change(ta, { target: { value: "hello" } });
      await act(async () => {
        fireEvent.click(screen.getByTitle("Send"));
      });
      expect(onSend).toHaveBeenCalledTimes(1);
      expect(ta.value).toBe("hello");
    });

    it("restores attachment chips on refusal", async () => {
      const onSend = vi.fn().mockResolvedValue({ accepted: false });
      render(<Composer {...defaultProps({ onSend })} />);
      await pickFiles([makeFile("spec.pdf", "application/pdf", 1024)]);
      await settlePipeline();
      expect(screen.getByTitle("Send").disabled).toBe(false);
      await act(async () => {
        fireEvent.click(screen.getByTitle("Send"));
      });
      expect(onSend).toHaveBeenCalledWith(
        "Take a look at the attached files.",
        [{ id: "att-mock" }],
      );
      // Chip restored after the refusal.
      const chip = screen.getByText("spec.pdf").closest(".attach-chip");
      expect(chip.getAttribute("data-status")).toBe("attached");
    });

    it("does not overwrite attachments added while the refusal was in flight", async () => {
      let resolveSend;
      const onSend = vi.fn(
        () =>
          new Promise((resolve) => {
            resolveSend = resolve;
          }),
      );
      render(<Composer {...defaultProps({ onSend })} />);
      await pickFiles([makeFile("first.pdf", "application/pdf", 1024)]);
      await settlePipeline();
      fireEvent.click(screen.getByTitle("Send"));
      expect(screen.queryByText("first.pdf")).toBeNull();
      // A new file arrives before the refusal lands.
      await pickFiles([makeFile("second.pdf", "application/pdf", 1024)]);
      await settlePipeline();
      await act(async () => {
        resolveSend({ accepted: false });
      });
      expect(screen.queryByText("first.pdf")).toBeNull();
      expect(screen.getByText("second.pdf")).toBeTruthy();
    });
  });
});
