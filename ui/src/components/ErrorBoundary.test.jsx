// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ErrorBoundary from "./ErrorBoundary.jsx";

function Boom() {
  throw new Error("boom");
}

describe("ErrorBoundary", () => {
  let consoleError;

  beforeEach(() => {
    // React logs the caught error to console.error; silence it so the
    // test output stays readable.
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
  });

  it("renders children when no error is thrown", async () => {
    await act(async () =>
      render(
        <ErrorBoundary>
          <p>healthy</p>
        </ErrorBoundary>,
      ),
    );
    expect(screen.getByText("healthy")).toBeTruthy();
    expect(screen.queryByTestId("error-boundary")).toBeNull();
  });

  it("catches a thrown render error and shows the friendly fallback", async () => {
    await act(async () =>
      render(
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>,
      ),
    );
    const fallback = screen.getByTestId("error-boundary");
    expect(fallback).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Something went wrong" })).toBeTruthy();
    // Reload button must be visible.
    expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy();
    // componentDidCatch logged the error with our specific prefix.
    // React itself also logs caught errors to console.error, so we
    // can't just assert the spy was called — distinguish on the
    // "ErrorBoundary caught:" prefix to confirm our handler ran.
    expect(
      consoleError.mock.calls.some(
        ([firstArg]) =>
          typeof firstArg === "string" && firstArg.includes("ErrorBoundary caught:"),
      ),
    ).toBe(true);
  });

  it("calls window.location.reload when the Reload button is clicked", async () => {
    const reload = vi.fn();
    vi.stubGlobal("location", { reload });

    await act(async () =>
      render(
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledTimes(1);

    vi.unstubAllGlobals();
  });

  it("treats undefined error.message as empty without crashing", async () => {
    // Defensive — covers the `error?.message ?? ""` branch in
    // getDerivedStateFromError.
    function ThrowsUndefined() {
      // eslint-disable-next-line no-throw-literal
      throw undefined;
    }
    await act(async () =>
      render(
        <ErrorBoundary>
          <ThrowsUndefined />
        </ErrorBoundary>,
      ),
    );
    expect(screen.getByTestId("error-boundary")).toBeTruthy();
  });

  it("componentDidCatch tolerates a missing globalThis.console", () => {
    // Drives the falsy branch of `if (globalThis.console)` — vitest 4's
    // AST-aware coverage flags this as a separately-coverable branch even
    // though the production path always has console available. We can't
    // simply undefine globalThis.console for the duration of a render —
    // React itself calls console.error to log caught errors and would
    // crash. Instead, drive componentDidCatch directly on a freshly-
    // constructed instance with the global swapped only for the call.
    const boundary = new ErrorBoundary({ children: null });
    const originalConsole = globalThis.console;
    Object.defineProperty(globalThis, "console", {
      configurable: true,
      value: undefined,
    });
    try {
      // No-throw is the assertion — the guard short-circuits cleanly.
      boundary.componentDidCatch(new Error("test"), { componentStack: "" });
    } finally {
      Object.defineProperty(globalThis, "console", {
        configurable: true,
        value: originalConsole,
      });
    }
  });

  it("handleReload is a no-op when globalThis.location is missing", () => {
    // Drives the falsy branch of `if (globalThis.location)` in handleReload.
    // Same pattern as the console test above — invoke the method directly
    // so we don't have to render through React's commit phase under a
    // missing-global condition.
    const boundary = new ErrorBoundary({ children: null });
    const originalLocation = globalThis.location;
    Object.defineProperty(globalThis, "location", {
      configurable: true,
      value: undefined,
    });
    try {
      // No-throw is the assertion — the guard short-circuits cleanly.
      boundary.handleReload();
    } finally {
      Object.defineProperty(globalThis, "location", {
        configurable: true,
        value: originalLocation,
      });
    }
  });
});
