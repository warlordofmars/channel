// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

vi.mock("../../api.js", () => ({
  listAssets: vi.fn(),
  getAsset: vi.fn(),
  getAssetContent: vi.fn(),
}));

import * as api from "../../api.js";
import Artifacts, { drainAssetPages } from "./Artifacts.jsx";

function card(over = {}) {
  return {
    asset_id: "as-1",
    chat_id: "ch-1",
    msg_id: "m-1",
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

function textResponse(text = "") {
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

function renderAt(path) {
  let lastSearch = null;
  function SearchCatcher() {
    const { search } = useLocation();
    lastSearch = search;
    return null;
  }
  const result = render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/artifacts" element={<><Artifacts /><SearchCatcher /></>} />
      </Routes>
    </MemoryRouter>,
  );
  return { ...result, getLastSearch: () => lastSearch };
}

describe("drainAssetPages", () => {
  beforeEach(() => {
    api.listAssets.mockReset();
  });

  it("returns the first page unchanged when it already has items", async () => {
    api.listAssets.mockResolvedValueOnce({ items: [card()], next_cursor: "c1" });
    const { items, nextCursor } = await drainAssetPages(null);
    expect(items).toHaveLength(1);
    expect(nextCursor).toBe("c1");
    expect(api.listAssets).toHaveBeenCalledTimes(1);
  });

  it("drains leading empty pages that carry a live cursor (never pages on count)", async () => {
    api.listAssets
      .mockResolvedValueOnce({ items: [], next_cursor: "c1" })
      .mockResolvedValueOnce({ items: [], next_cursor: "c2" })
      .mockResolvedValueOnce({ items: [card({ asset_id: "as-9" })], next_cursor: null });
    const { items, nextCursor } = await drainAssetPages(null);
    expect(items.map((a) => a.asset_id)).toEqual(["as-9"]);
    expect(nextCursor).toBeNull();
    expect(api.listAssets).toHaveBeenCalledTimes(3);
  });

  it("stops on a null cursor even when the final page is empty", async () => {
    api.listAssets.mockResolvedValueOnce({ items: [], next_cursor: null });
    const { items, nextCursor } = await drainAssetPages(null);
    expect(items).toEqual([]);
    expect(nextCursor).toBeNull();
  });

  it("treats a missing next_cursor field as exhausted", async () => {
    api.listAssets.mockResolvedValueOnce({ items: [card()] });
    const { nextCursor } = await drainAssetPages(null);
    expect(nextCursor).toBeNull();
  });
});

describe("Artifacts", () => {
  beforeEach(() => {
    api.listAssets.mockReset();
    api.getAsset.mockReset();
    api.getAssetContent.mockReset();
    api.getAssetContent.mockResolvedValue(textResponse("x = 1"));
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows a loading state before the first page lands", async () => {
    let resolve;
    api.listAssets.mockReturnValueOnce(new Promise((r) => { resolve = r; }));
    renderAt("/app/artifacts");
    expect(screen.getByText(/Loading artifacts/i)).toBeTruthy();
    resolve({ items: [], next_cursor: null });
    await waitFor(() => expect(screen.queryByText(/Loading artifacts/i)).toBeNull());
  });

  it("renders the header + one .list-row per returned asset, newest-first", async () => {
    api.listAssets.mockResolvedValueOnce({
      items: [card({ asset_id: "as-1", title: "newest.py" }), card({ asset_id: "as-2", title: "older.md", kind: "document" })],
      next_cursor: null,
    });
    const { container } = renderAt("/app/artifacts");
    await screen.findByText("newest.py");
    expect(screen.getByRole("heading", { level: 2, name: "Artifacts" })).toBeTruthy();
    const rows = container.querySelectorAll(".list-row");
    expect(rows.length).toBe(2);
    expect(rows[0].querySelector(".ti").textContent).toBe("newest.py");
    expect(rows[1].querySelector(".ti").textContent).toBe("older.md");
  });

  it("each row's sub-line shows kind label · size · relative time", async () => {
    api.listAssets.mockResolvedValueOnce({
      items: [card({ size_bytes: 2048 })],
      next_cursor: null,
    });
    const { container } = renderAt("/app/artifacts");
    await screen.findByText("rate-limiter.py");
    const sub = container.querySelector(".list-row .sub").textContent;
    expect(sub).toContain("Code");
    expect(sub).toContain("2.0 KB");
    expect(sub).toContain("just now");
  });

  it("renders an empty state when the browse returns no assets", async () => {
    api.listAssets.mockResolvedValueOnce({ items: [], next_cursor: null });
    renderAt("/app/artifacts");
    expect(await screen.findByText(/No artifacts yet/i)).toBeTruthy();
  });

  it("renders an error state when the first page rejects", async () => {
    api.listAssets.mockRejectedValueOnce(new Error("boom"));
    renderAt("/app/artifacts");
    expect(await screen.findByText(/Couldn't load your artifacts/i)).toBeTruthy();
  });

  it("no panel overlay by default (no ?artifact=)", async () => {
    api.listAssets.mockResolvedValueOnce({ items: [card()], next_cursor: null });
    const { container } = renderAt("/app/artifacts");
    await screen.findByText("rate-limiter.py");
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
  });

  it("clicking a row sets ?artifact=&chat= and opens the panel from the loaded card", async () => {
    api.listAssets.mockResolvedValueOnce({
      items: [card({ asset_id: "as-7", chat_id: "ch-3" })],
      next_cursor: null,
    });
    const { container, getLastSearch } = renderAt("/app/artifacts");
    fireEvent.click(await screen.findByText("rate-limiter.py"));
    await waitFor(() => expect(container.querySelector(".art-panel-wrap")).toBeTruthy());
    expect(getLastSearch()).toBe("?artifact=as-7&chat=ch-3");
    // Opened from the loaded card — no descriptor round-trip.
    expect(api.getAsset).not.toHaveBeenCalled();
  });

  it("opens a row via keyboard (Enter / Space) and ignores other keys", async () => {
    api.listAssets.mockResolvedValueOnce({
      items: [card({ asset_id: "as-7", chat_id: "ch-3" })],
      next_cursor: null,
    });
    const { container, getLastSearch } = renderAt("/app/artifacts");
    const row = await screen.findByRole("button", { name: /rate-limiter\.py/ });
    // A non-activating key is a no-op.
    fireEvent.keyDown(row, { key: "a" });
    expect(getLastSearch()).toBe("");
    // Enter opens the panel.
    fireEvent.keyDown(row, { key: "Enter" });
    await waitFor(() => expect(container.querySelector(".art-panel-wrap")).toBeTruthy());
    expect(getLastSearch()).toBe("?artifact=as-7&chat=ch-3");
    // Space also activates (covers the second branch).
    fireEvent.keyDown(row, { key: " " });
    expect(getLastSearch()).toBe("?artifact=as-7&chat=ch-3");
  });

  it("Close clears both params and removes the panel", async () => {
    api.listAssets.mockResolvedValueOnce({
      items: [card({ asset_id: "as-7", chat_id: "ch-3" })],
      next_cursor: null,
    });
    const { container, getLastSearch } = renderAt("/app/artifacts?artifact=as-7&chat=ch-3");
    await waitFor(() => expect(container.querySelector(".art-panel-wrap")).toBeTruthy());
    fireEvent.click(screen.getByTitle("Close"));
    expect(getLastSearch()).toBe("");
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
  });

  it("cold deep-link fetches the descriptor when the asset isn't on the loaded page", async () => {
    api.listAssets.mockResolvedValueOnce({ items: [card({ asset_id: "as-1" })], next_cursor: null });
    api.getAsset.mockResolvedValueOnce(card({ asset_id: "as-9", chat_id: "ch-9", title: "deep.md", kind: "document" }));
    const { container } = renderAt("/app/artifacts?artifact=as-9&chat=ch-9");
    await waitFor(() => expect(container.querySelector(".art-panel-wrap")).toBeTruthy());
    expect(api.getAsset).toHaveBeenCalledWith("ch-9", "as-9");
    expect(screen.getByText("deep.md")).toBeTruthy();
  });

  it("a failed descriptor fetch on deep-link leaves the panel closed", async () => {
    api.listAssets.mockResolvedValueOnce({ items: [], next_cursor: null });
    api.getAsset.mockRejectedValueOnce(new Error("404"));
    const { container } = renderAt("/app/artifacts?artifact=as-9&chat=ch-9");
    await screen.findByText(/No artifacts yet/i);
    await waitFor(() => expect(api.getAsset).toHaveBeenCalled());
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
  });

  it("a partial deep-link (artifact without chat) opens no panel and fetches no descriptor", async () => {
    api.listAssets.mockResolvedValueOnce({ items: [], next_cursor: null });
    const { container } = renderAt("/app/artifacts?artifact=as-9");
    await screen.findByText(/No artifacts yet/i);
    expect(api.getAsset).not.toHaveBeenCalled();
    expect(container.querySelector(".art-panel-wrap")).toBeNull();
  });

  it("shows Load more while a cursor is live and appends the next page on click", async () => {
    api.listAssets
      .mockResolvedValueOnce({ items: [card({ asset_id: "as-1", title: "page1.py" })], next_cursor: "c1" })
      .mockResolvedValueOnce({ items: [card({ asset_id: "as-2", title: "page2.py" })], next_cursor: null });
    const { container } = renderAt("/app/artifacts");
    await screen.findByText("page1.py");
    const btn = screen.getByRole("button", { name: "Load more" });
    fireEvent.click(btn);
    await screen.findByText("page2.py");
    expect(container.querySelectorAll(".list-row").length).toBe(2);
    // Cursor exhausted → the button is gone.
    expect(screen.queryByRole("button", { name: /Load more/ })).toBeNull();
  });

  it("no Load more button when the first page already exhausts the cursor", async () => {
    api.listAssets.mockResolvedValueOnce({ items: [card()], next_cursor: null });
    renderAt("/app/artifacts");
    await screen.findByText("rate-limiter.py");
    expect(screen.queryByRole("button", { name: /Load more/ })).toBeNull();
  });

  it("keeps the list + button when a Load more page rejects", async () => {
    api.listAssets
      .mockResolvedValueOnce({ items: [card({ title: "page1.py" })], next_cursor: "c1" })
      .mockRejectedValueOnce(new Error("boom"));
    renderAt("/app/artifacts");
    await screen.findByText("page1.py");
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    // Button returns to idle and the first page is still shown.
    await waitFor(() => expect(screen.getByRole("button", { name: "Load more" })).toBeTruthy());
    expect(screen.getByText("page1.py")).toBeTruthy();
  });

  it("ignores a re-click while a Load more page is still in flight", async () => {
    const d = deferred();
    api.listAssets
      .mockResolvedValueOnce({ items: [card({ title: "page1.py" })], next_cursor: "c1" })
      .mockReturnValueOnce(d.promise);
    renderAt("/app/artifacts");
    await screen.findByText("page1.py");
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    // The in-flight click flipped the label; a re-click is a no-op.
    fireEvent.click(screen.getByRole("button", { name: "Loading…" }));
    await act(async () => {
      d.resolve({ items: [card({ asset_id: "as-2", title: "page2.py" })], next_cursor: null });
      await Promise.resolve();
    });
    await screen.findByText("page2.py");
    // First page + exactly one Load more fetch — the re-click didn't fire.
    expect(api.listAssets).toHaveBeenCalledTimes(2);
  });

  it("ignores the first page when the view unmounts before it lands", async () => {
    const d = deferred();
    api.listAssets.mockReturnValueOnce(d.promise);
    const { unmount } = renderAt("/app/artifacts");
    unmount();
    await act(async () => {
      d.resolve({ items: [card()], next_cursor: null });
      await Promise.resolve();
    });
    expect(api.listAssets).toHaveBeenCalledTimes(1);
  });

  it("ignores a first-page error that lands after unmount", async () => {
    const d = deferred();
    api.listAssets.mockReturnValueOnce(d.promise);
    const { unmount } = renderAt("/app/artifacts");
    unmount();
    await act(async () => {
      d.reject(new Error("late"));
      await Promise.resolve();
    });
    expect(api.listAssets).toHaveBeenCalledTimes(1);
  });

  it("ignores a deep-link descriptor that resolves after unmount", async () => {
    api.listAssets.mockResolvedValueOnce({ items: [], next_cursor: null });
    const d = deferred();
    api.getAsset.mockReturnValueOnce(d.promise);
    const { unmount } = renderAt("/app/artifacts?artifact=as-9&chat=ch-9");
    await waitFor(() => expect(api.getAsset).toHaveBeenCalled());
    unmount();
    await act(async () => {
      d.resolve(card({ asset_id: "as-9" }));
      await Promise.resolve();
    });
    expect(api.getAsset).toHaveBeenCalledTimes(1);
  });

  it("ignores a deep-link descriptor error that lands after unmount", async () => {
    api.listAssets.mockResolvedValueOnce({ items: [], next_cursor: null });
    const d = deferred();
    api.getAsset.mockReturnValueOnce(d.promise);
    const { unmount } = renderAt("/app/artifacts?artifact=as-9&chat=ch-9");
    await waitFor(() => expect(api.getAsset).toHaveBeenCalled());
    unmount();
    await act(async () => {
      d.reject(new Error("late"));
      await Promise.resolve();
    });
    expect(api.getAsset).toHaveBeenCalledTimes(1);
  });
});
