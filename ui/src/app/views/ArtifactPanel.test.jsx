// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api.js", () => ({
  getAssetContent: vi.fn(),
}));

import * as api from "../../api.js";
import ArtifactPanel, {
  copyAssetText,
  parseCsv,
  triggerBlobDownload,
} from "./ArtifactPanel.jsx";

function card(over = {}) {
  return {
    asset_id: "as-1",
    chat_id: "ch-1",
    kind: "code",
    title: "rate-limiter.py",
    mime: "text/x-python",
    size_bytes: 2048,
    origin: "generated",
    created_at: new Date().toISOString(),
    source: { msg_id: "m-1" },
    ...over,
  };
}

function textResponse(text) {
  return { text: async () => text, blob: async () => new Blob([text]) };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function mountPanel(assetOver, onClose = () => {}) {
  return render(<ArtifactPanel artifact={card(assetOver)} onClose={onClose} />);
}

beforeEach(() => {
  api.getAssetContent.mockReset();
  api.getAssetContent.mockResolvedValue(textResponse(""));
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
  // Neuter the transient download anchor's click so jsdom doesn't warn
  // about "navigation (except hash changes)" on every Download test.
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
    writable: true,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ArtifactPanel — mounting", () => {
  it("renders nothing when artifact is null", () => {
    const { container } = render(<ArtifactPanel artifact={null} onClose={() => {}} />);
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
    expect(api.getAssetContent).not.toHaveBeenCalled();
  });

  it("renders the header title + kind·size sub-line", async () => {
    api.getAssetContent.mockResolvedValue(textResponse("x = 1"));
    mountPanel({ kind: "code", title: "rate-limiter.py", size_bytes: 2048 });
    expect(screen.getByText("rate-limiter.py")).toBeTruthy();
    expect(screen.getByText("Code · 2.0 KB")).toBeTruthy();
    await screen.findByText(/x = 1/);
  });

  it("drops the size from the sub-line when size_bytes is unavailable", async () => {
    api.getAssetContent.mockResolvedValue(textResponse("x = 1"));
    mountPanel({ kind: "code", size_bytes: null });
    expect(document.querySelector(".art-phead .s").textContent).toBe("Code");
    await screen.findByText(/x = 1/);
  });
});

describe("ArtifactPanel — renderers", () => {
  it("code → <pre><code> from the text payload", async () => {
    api.getAssetContent.mockResolvedValue(textResponse("export const x = 1;"));
    mountPanel({ kind: "code" });
    await screen.findByText(/export const x = 1;/);
    expect(document.querySelector("pre.art-code code")).toBeTruthy();
  });

  it("document → markdown via renderMarkdown", async () => {
    api.getAssetContent.mockResolvedValue(textResponse("# Summary\n\nA runbook."));
    mountPanel({ kind: "document", mime: "text/markdown" });
    await screen.findByText("Summary");
    expect(document.querySelector(".art-doc")).toBeTruthy();
    expect(screen.getByText("A runbook.")).toBeTruthy();
  });

  it("data → a table with a header row + one row per data line", async () => {
    api.getAssetContent.mockResolvedValue(textResponse("Month,Spend\nJan,$10\nFeb,$20"));
    mountPanel({ kind: "data", mime: "text/csv" });
    const table = await screen.findByRole("table");
    expect(table.classList.contains("art-table")).toBe(true);
    expect(table.querySelectorAll("thead th").length).toBe(2);
    expect(table.querySelectorAll("tbody tr").length).toBe(2);
  });

  it("data → an 'empty' notice when the CSV body is blank", async () => {
    api.getAssetContent.mockResolvedValue(textResponse("   "));
    mountPanel({ kind: "data" });
    expect(await screen.findByText(/This data artifact is empty/i)).toBeTruthy();
  });

  it("image → an <img> backed by a blob object URL", async () => {
    api.getAssetContent.mockResolvedValue(textResponse("PNGBYTES"));
    mountPanel({ kind: "image", mime: "image/png" });
    const img = await screen.findByRole("img");
    expect(img.getAttribute("src")).toBe("blob:mock");
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("diagram → the download-only fallback (no content fetch)", () => {
    mountPanel({ kind: "diagram", mime: "text/vnd.mermaid" });
    expect(screen.getByText(/Preview isn't available/i)).toBeTruthy();
    expect(api.getAssetContent).not.toHaveBeenCalled();
  });

  it("unknown kind → the download-only fallback", () => {
    mountPanel({ kind: "mystery" });
    expect(document.querySelector(".art-fallback")).toBeTruthy();
    expect(api.getAssetContent).not.toHaveBeenCalled();
  });

  it("shows a loading state until the payload resolves", async () => {
    const d = deferred();
    api.getAssetContent.mockReturnValueOnce(d.promise);
    mountPanel({ kind: "code" });
    expect(screen.getByText("Loading…")).toBeTruthy();
    await act(async () => {
      d.resolve(textResponse("done"));
      await Promise.resolve();
    });
    await screen.findByText(/done/);
  });
});

describe("ArtifactPanel — content-fetch failures", () => {
  it("404 → 'no longer available' copy", async () => {
    api.getAssetContent.mockRejectedValue({ status: 404 });
    mountPanel({ kind: "code" });
    expect(await screen.findByText(/no longer available/i)).toBeTruthy();
  });

  it("a status-less failure → the generic retry copy", async () => {
    api.getAssetContent.mockRejectedValue(undefined);
    mountPanel({ kind: "image" });
    expect(await screen.findByText(/Couldn't load this artifact/i)).toBeTruthy();
  });
});

describe("ArtifactPanel — copy", () => {
  it("copies the fetched text and flips the icon to 'Copied', then reverts", async () => {
    vi.useFakeTimers();
    api.getAssetContent.mockResolvedValue(textResponse("x = 1"));
    mountPanel({ kind: "code" });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fireEvent.click(screen.getByTitle("Copy"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("x = 1");
    expect(screen.getByTitle("Copied")).toBeTruthy();
    // A second copy while the feedback timer is still pending clears the
    // prior timer before scheduling a fresh one.
    fireEvent.click(screen.getByTitle("Copied"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(navigator.clipboard.writeText).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(screen.getByTitle("Copy")).toBeTruthy();
    vi.useRealTimers();
  });

  it("a clipboard write failure leaves the icon on 'Copy'", async () => {
    navigator.clipboard.writeText.mockRejectedValue(new Error("denied"));
    api.getAssetContent.mockResolvedValue(textResponse("x = 1"));
    mountPanel({ kind: "code" });
    await screen.findByText(/x = 1/);
    fireEvent.click(screen.getByTitle("Copy"));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalled());
    expect(screen.getByTitle("Copy")).toBeTruthy();
    expect(screen.queryByTitle("Copied")).toBeNull();
  });

  it("no Copy button for a non-text (image) asset", async () => {
    api.getAssetContent.mockResolvedValue(textResponse("PNG"));
    mountPanel({ kind: "image" });
    await screen.findByRole("img");
    expect(screen.queryByTitle("Copy")).toBeNull();
  });
});

describe("ArtifactPanel — download", () => {
  it("Download streams a fresh blob and triggers a save", async () => {
    api.getAssetContent.mockResolvedValue(textResponse("x = 1"));
    mountPanel({ kind: "code", title: "rate-limiter.py" });
    await screen.findByText(/x = 1/);
    api.getAssetContent.mockClear();
    fireEvent.click(screen.getByTitle("Download"));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
    expect(api.getAssetContent).toHaveBeenCalledWith("ch-1", "as-1");
  });

  it("the fallback body's Download button also saves", async () => {
    const { container } = mountPanel({ kind: "diagram" });
    api.getAssetContent.mockResolvedValue(textResponse("mermaid"));
    fireEvent.click(container.querySelector(".art-fallback button"));
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalled());
  });

  it("a failed download surfaces nothing (no throw)", async () => {
    api.getAssetContent.mockResolvedValueOnce(textResponse("x = 1"));
    mountPanel({ kind: "code" });
    await screen.findByText(/x = 1/);
    api.getAssetContent.mockRejectedValueOnce(new Error("network"));
    fireEvent.click(screen.getByTitle("Download"));
    await waitFor(() => expect(api.getAssetContent).toHaveBeenCalledTimes(2));
    // Panel is still intact.
    expect(document.querySelector(".art-panel-wrap")).toBeTruthy();
  });
});

describe("ArtifactPanel — close + cancellation", () => {
  it("Close and backdrop both fire onClose", async () => {
    api.getAssetContent.mockResolvedValue(textResponse("x = 1"));
    const onClose = vi.fn();
    const { container } = mountPanel({ kind: "code" }, onClose);
    await screen.findByText(/x = 1/);
    fireEvent.click(screen.getByTitle("Close"));
    fireEvent.click(container.querySelector(".art-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("cancels an in-flight image fetch on asset change and revokes the stale blob", async () => {
    const d = deferred();
    api.getAssetContent.mockReturnValueOnce(d.promise);
    const { rerender } = render(
      <ArtifactPanel artifact={card({ kind: "image", asset_id: "img-1" })} onClose={() => {}} />,
    );
    api.getAssetContent.mockResolvedValueOnce(textResponse("x = 1"));
    rerender(
      <ArtifactPanel artifact={card({ kind: "code", asset_id: "code-2" })} onClose={() => {}} />,
    );
    await act(async () => {
      d.resolve(textResponse("late-image"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock");
  });

  it("ignores a text fetch that resolves after the panel closes", async () => {
    const d = deferred();
    api.getAssetContent.mockReturnValueOnce(d.promise);
    const { rerender } = render(
      <ArtifactPanel artifact={card({ kind: "code", asset_id: "code-1" })} onClose={() => {}} />,
    );
    rerender(<ArtifactPanel artifact={null} onClose={() => {}} />);
    await act(async () => {
      d.resolve(textResponse("late"));
      await Promise.resolve();
    });
    expect(document.querySelector(".art-panel-wrap")).toBeNull();
  });

  it("ignores a fetch rejection that lands after the panel closes", async () => {
    const d = deferred();
    api.getAssetContent.mockReturnValueOnce(d.promise);
    const { rerender } = render(
      <ArtifactPanel artifact={card({ kind: "code", asset_id: "code-1" })} onClose={() => {}} />,
    );
    rerender(<ArtifactPanel artifact={null} onClose={() => {}} />);
    await act(async () => {
      d.reject(new Error("late-fail"));
      await Promise.resolve();
    });
    expect(document.querySelector(".art-error")).toBeNull();
  });
});

describe("ArtifactPanel — exported helpers", () => {
  it("parseCsv splits rows on newlines and cells on commas, dropping blank lines", () => {
    expect(parseCsv("a,b\n1,2\n\n3,4\n")).toEqual([
      ["a", "b"],
      ["1", "2"],
      ["3", "4"],
    ]);
    expect(parseCsv("   ")).toEqual([]);
  });

  it("copyAssetText returns true on success and false on failure", async () => {
    await expect(copyAssetText("hi")).resolves.toBe(true);
    navigator.clipboard.writeText.mockRejectedValueOnce(new Error("no"));
    await expect(copyAssetText("hi")).resolves.toBe(false);
  });

  it("triggerBlobDownload names the anchor, clicks it, then revokes the URL", () => {
    triggerBlobDownload(new Blob(["x"]), "report.csv");
    expect(URL.createObjectURL).toHaveBeenCalled();
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock");
  });

  it("triggerBlobDownload defaults the filename to 'artifact' when none is given", () => {
    const created = [];
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag) => {
      const el = realCreate(tag);
      if (tag === "a") created.push(el);
      return el;
    });
    triggerBlobDownload(new Blob(["x"]), "");
    expect(created[0].download).toBe("artifact");
  });
});
