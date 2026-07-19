// Copyright (c) 2026 John Carter. All rights reserved.
import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// mermaid is heavy and DOM-driven; the SVG bytes are meaningless under jsdom,
// so we mock it and assert the contract: render() is called with the source
// string, and the resolved SVG / rejection drives the two render branches.
vi.mock("mermaid", () => ({
  default: { initialize: vi.fn(), render: vi.fn() },
}));

import mermaid from "mermaid";
import MermaidBlock from "./MermaidBlock.jsx";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("MermaidBlock", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mermaid.render.mockResolvedValue({
      svg: "<svg><text>ok diagram</text></svg>",
    });
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    document.documentElement.removeAttribute("data-theme");
  });

  it("renders the mermaid-produced SVG for a valid diagram", async () => {
    const src = "graph TD; A-->B";
    let container;
    await act(async () => {
      ({ container } = render(<MermaidBlock source={src} />));
    });
    const block = container.querySelector(".mermaid-block");
    await waitFor(() => expect(block.innerHTML).toContain("ok diagram"));
    // The diagram source is handed to mermaid verbatim (2nd positional arg).
    expect(mermaid.render).toHaveBeenCalledTimes(1);
    expect(mermaid.render.mock.calls[0][1]).toBe(src);
    // No parse-error fallback on the happy path.
    expect(container.querySelector(".mermaid-error")).toBeNull();
  });

  it("initialises mermaid with the neutral theme in light mode", async () => {
    await act(async () => {
      render(<MermaidBlock source="graph TD; A-->B" />);
    });
    expect(mermaid.initialize).toHaveBeenCalledWith(
      expect.objectContaining({ theme: "neutral", securityLevel: "strict" }),
    );
  });

  it("initialises mermaid with the dark theme when data-theme=dark", async () => {
    document.documentElement.setAttribute("data-theme", "dark");
    await act(async () => {
      render(<MermaidBlock source="graph TD; A-->B" />);
    });
    expect(mermaid.initialize).toHaveBeenCalledWith(
      expect.objectContaining({ theme: "dark" }),
    );
  });

  it("falls back to raw source + caption when mermaid rejects", async () => {
    mermaid.render.mockRejectedValue(new Error("parse fail"));
    const src = "graph TD; A--";
    let container;
    await act(async () => {
      ({ container } = render(<MermaidBlock source={src} />));
    });
    await waitFor(() =>
      expect(container.querySelector(".mermaid-error")).toBeTruthy(),
    );
    expect(container.querySelector(".mermaid-source code").textContent).toBe(
      src,
    );
    expect(
      container.querySelector(".mermaid-error-caption").textContent,
    ).toBe("Mermaid parse error");
    // No SVG injected on the error path.
    expect(container.querySelector(".mermaid-block").innerHTML).not.toContain(
      "svg",
    );
  });

  it("drops a resolve that lands after unmount (cancelled guard)", async () => {
    const d = deferred();
    mermaid.render.mockReturnValue(d.promise);
    let unmount;
    await act(async () => {
      ({ unmount } = render(<MermaidBlock source="graph TD; A-->B" />));
    });
    unmount();
    // Resolving after unmount hits the `cancelled` branch of onRendered — the
    // state setter is never called, so no post-unmount update escapes.
    await act(async () => {
      d.resolve({ svg: "<svg><text>late</text></svg>" });
      await d.promise;
    });
    expect(mermaid.render).toHaveBeenCalledTimes(1);
  });

  it("drops a reject that lands after unmount (cancelled guard)", async () => {
    const d = deferred();
    mermaid.render.mockReturnValue(d.promise);
    let unmount;
    await act(async () => {
      ({ unmount } = render(<MermaidBlock source="graph TD; A-->B" />));
    });
    unmount();
    await act(async () => {
      d.reject(new Error("late fail"));
      await d.promise.catch(() => {});
    });
    expect(mermaid.render).toHaveBeenCalledTimes(1);
  });

  it("uses a unique render id per diagram instance", async () => {
    await act(async () => {
      render(<MermaidBlock source="graph TD; A-->B" />);
    });
    await act(async () => {
      render(<MermaidBlock source="graph LR; C-->D" />);
    });
    expect(mermaid.render.mock.calls[0][0]).not.toBe(
      mermaid.render.mock.calls[1][0],
    );
  });
});
