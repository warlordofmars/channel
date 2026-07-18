// Copyright (c) 2026 John Carter. All rights reserved.
//
// Reference example for the `react-component` skill.
//
// This file is documentation, not application code — it lives under
// `.claude/skills/` and is not imported by the running app or tests.
// Copy it into `ui/src/components/<Name>.jsx` (and the companion test
// block at the bottom into `ui/src/components/<Name>.test.jsx`) and
// adapt the names when adding a real component.
//
// Mirrors every convention captured in `SKILL.md`:
//
//   1. Hand-rolled primitives — a plain `<button>` styled via a CSS
//      class + tokens (Channel has no shadcn/ui `Button`).
//   2. CSS variables — colours via `var(--ink)`, `var(--border)`,
//      `var(--raised)`, `var(--accent)`, `var(--danger)`; never hex.
//   3. Icons — `Icon` from `ui/src/components/Icon.jsx` (the 24×24
//      stroke set), never `lucide-react`, never emoji.
//   4. Co-located tests — see the companion test block below.
//   5. Vitest gotchas — named handlers (no anonymous arrows on
//      `onClick`), and `vi.useFakeTimers()` activated *before*
//      `render(...)` because the timer is scheduled in the mount
//      effect (Case A in SKILL.md §5.2).
//   6. Copyright header — the line above.
//   7. Pre-push gate — `uv run inv pre-push` before PR.
//
// ---------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------

import React, { useEffect, useRef, useState } from "react";
import Icon from "@/components/Icon.jsx";

// Export the constant so the companion test can import it and stay
// in sync if the duration changes — see the test block below.
export const AUTO_DISMISS_MS = 5000;

/**
 * Toast — a transient confirmation banner.
 *
 * Demonstrates the full convention set: a plain `<button>` for the
 * dismiss action, `var(--*)` tokens for every colour, an `Icon.jsx`
 * glyph for the status marker, and named handlers (`handleDismiss`,
 * `onAutoDismiss`, `cleanup`) so the v8 counter sees a small,
 * fixed set of named functions instead of one anonymous closure
 * per call site. Naming alone does not satisfy the 100% functions
 * threshold — the companion test must still drive each handler's
 * body — but it removes the per-call-site uncovered-function
 * counter that anonymous arrows create.
 */
export default function Toast({ status, message, onClose }) {
  const [visible, setVisible] = useState(true);
  // Hold the timeout handle in a ref so handleDismiss() can clear
  // it without waiting for unmount — otherwise the auto-dismiss
  // timer keeps running after a manual dismiss and onClose fires
  // twice. The component stays mounted while visible === false
  // (we render null), so the useEffect cleanup alone is not
  // sufficient.
  const timerRef = useRef(null);

  // Side effect with named callbacks — see SKILL.md §5.3. The three
  // names (`scheduleAutoDismiss`, `onAutoDismiss`, `cleanup`) each
  // get their own v8 coverage counter; an anonymous arrow on any of
  // them would fail the 100% gate.
  useEffect(function scheduleAutoDismiss() {
    function onAutoDismiss() {
      timerRef.current = null;
      setVisible(false);
      onClose?.();
    }
    timerRef.current = globalThis.setTimeout(onAutoDismiss, AUTO_DISMISS_MS);
    return function cleanup() {
      if (timerRef.current !== null) {
        globalThis.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [onClose]);

  function handleDismiss() {
    if (timerRef.current !== null) {
      globalThis.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setVisible(false);
    onClose?.();
  }

  if (!visible) return null;

  // Icon.jsx renders currentColor SVGs, so the glyph inherits the
  // wrapping element's `color`. Status colour comes from a token,
  // not a hex literal.
  const iconName = status === "success" ? "check" : "close";
  const iconColour =
    status === "success" ? "var(--accent)" : "var(--danger)";

  return (
    <div
      role="status"
      aria-live="polite"
      className="toast"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.75rem",
        border: "1px solid var(--border)",
        background: "var(--raised)",
        color: "var(--ink)",
        borderRadius: "var(--r-md)",
        padding: "0.75rem 1rem",
      }}
    >
      <span style={{ color: iconColour, display: "inline-flex" }}>
        <Icon name={iconName} size={18} />
      </span>
      <span>{message}</span>
      <button type="button" className="toast-dismiss" onClick={handleDismiss}>
        Dismiss
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------
// Companion test — copy this block into
// `ui/src/components/Toast.test.jsx` next to the component above.
//
// Covers, at minimum:
//   - Initial render branch (visible)                  → §4.1
//   - Conditional render branch (hidden after dismiss) → §4.2
//   - onClick handler path (handleDismiss)             → §4.3
//   - useEffect callback path (auto-dismiss timer)     → §4.4 + §5.2
//   - Status branch (success vs. error icon)
//   - Dismiss-clears-timer regression                  → no double onClose
// ---------------------------------------------------------------------
//
// // Copyright (c) 2026 John Carter. All rights reserved.
// import { act, fireEvent, render, screen } from "@testing-library/react";
// import { afterEach, describe, expect, it, vi } from "vitest";
// import Toast, { AUTO_DISMISS_MS } from "./Toast.jsx";
//
// describe("Toast", () => {
//   afterEach(() => {
//     vi.useRealTimers();      // always restore — see SKILL.md §5.2
//     vi.restoreAllMocks();    // restore vi.spyOn(...) globals between tests
//   });
//
//   it("renders the message on mount", async () => {
//     await act(async () => render(<Toast status="success" message="Saved." />));
//     expect(screen.getByText("Saved.")).toBeTruthy();
//   });
//
//   it("renders the success icon when status is success", async () => {
//     const { container } = await act(async () =>
//       render(<Toast status="success" message="OK" />)
//     );
//     // Icon.jsx renders currentColor SVGs; the colour lives on the
//     // wrapping <span>, so read it from the svg's parentElement.
//     const wrapper = container.querySelector("svg").parentElement;
//     // jsdom normalises var() resolution differently than the browser, so
//     // assert the *style* attribute string contains the token name rather
//     // than a resolved rgb() value. The hex→rgb gotcha (§5.1) only applies
//     // to literal hex values; var() references stay as-is.
//     expect(wrapper.getAttribute("style")).toContain("var(--accent)");
//   });
//
//   it("renders the error icon when status is error", async () => {
//     const { container } = await act(async () =>
//       render(<Toast status="error" message="Nope." />)
//     );
//     expect(container.querySelector("svg").parentElement.getAttribute("style"))
//       .toContain("var(--danger)");
//   });
//
//   it("hides itself when Dismiss is clicked (covers handleDismiss)", async () => {
//     const onClose = vi.fn();
//     await act(async () =>
//       render(<Toast status="success" message="Saved." onClose={onClose} />)
//     );
//     fireEvent.click(screen.getByText("Dismiss"));
//     expect(screen.queryByText("Saved.")).toBeNull();
//     expect(onClose).toHaveBeenCalledTimes(1);
//   });
//
//   it("does not double-fire onClose when Dismiss precedes the auto-timer", async () => {
//     // Regression: handleDismiss must clear the pending timeout. If it
//     // doesn't, onAutoDismiss runs later and calls onClose a second time.
//     vi.useFakeTimers();
//     const onClose = vi.fn();
//     await act(async () =>
//       render(<Toast status="success" message="Saved." onClose={onClose} />)
//     );
//     fireEvent.click(screen.getByText("Dismiss"));
//     await act(async () => vi.advanceTimersByTime(AUTO_DISMISS_MS));
//     expect(onClose).toHaveBeenCalledTimes(1);
//   });
//
//   it("auto-dismisses after 5 seconds (covers scheduleAutoDismiss + onAutoDismiss)", async () => {
//     // Activate fake timers FIRST — see SKILL.md §5.2 Case A. Toast's
//     // setTimeout is scheduled in its mount useEffect, so fake timers
//     // must be active before render() to intercept that scheduling.
//     // Activating after mount leaves the timer on the real clock and
//     // advanceTimersByTime never fires it.
//     vi.useFakeTimers();
//     const onClose = vi.fn();
//     await act(async () =>
//       render(<Toast status="success" message="Saved." onClose={onClose} />)
//     );
//     await act(async () => vi.advanceTimersByTime(AUTO_DISMISS_MS));
//     expect(screen.queryByText("Saved.")).toBeNull();
//     expect(onClose).toHaveBeenCalledTimes(1);
//   });
//
//   it("clears its timer on unmount (covers cleanup)", async () => {
//     const clearSpy = vi.spyOn(globalThis, "clearTimeout");
//     const { unmount } = await act(async () =>
//       render(<Toast status="success" message="Saved." />)
//     );
//     unmount();
//     expect(clearSpy).toHaveBeenCalled();
//   });
// });
