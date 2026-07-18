// Copyright (c) 2026 John Carter. All rights reserved.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";

vi.mock("../api.js", () => ({
  getAssetContent: vi.fn(),
}));

import * as api from "../api.js";
import { useAssetContent } from "./useAssetContent.js";

function blobResponse(text = "BYTES") {
  return { blob: async () => new Blob([text]), text: async () => text };
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

beforeEach(() => {
  api.getAssetContent.mockReset();
  api.getAssetContent.mockResolvedValue(blobResponse());
  URL.createObjectURL.mockClear();
  URL.revokeObjectURL.mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useAssetContent", () => {
  it("fetches in blob mode and exposes an object URL when ready", async () => {
    const { result } = renderHook(() =>
      useAssetContent("c1", "a1", { mode: "blob" }),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.url).toBe("blob:test-url");
    expect(result.current.text).toBeNull();
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(api.getAssetContent).toHaveBeenCalledWith("c1", "a1");
  });

  it("defaults to blob mode when no options are given", async () => {
    const { result } = renderHook(() => useAssetContent("c1", "a1"));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.url).toBe("blob:test-url");
  });

  it("fetches in text mode and exposes the decoded text", async () => {
    api.getAssetContent.mockResolvedValue(blobResponse("# hello"));
    const { result } = renderHook(() =>
      useAssetContent("c1", "a1", { mode: "text" }),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.text).toBe("# hello");
    expect(result.current.url).toBeNull();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("reports error with the HTTP status when the fetch rejects", async () => {
    api.getAssetContent.mockRejectedValue({ status: 404 });
    const { result } = renderHook(() =>
      useAssetContent("c1", "a1", { mode: "blob" }),
    );
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.httpStatus).toBe(404);
  });

  it("reports error with a null status when the rejection carries none", async () => {
    api.getAssetContent.mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() =>
      useAssetContent("c1", "a1", { mode: "text" }),
    );
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.httpStatus).toBeNull();
  });

  it("stays idle and never fetches when disabled", () => {
    const { result } = renderHook(() =>
      useAssetContent("c1", "a1", { mode: "blob", enabled: false }),
    );
    expect(result.current.status).toBe("idle");
    expect(api.getAssetContent).not.toHaveBeenCalled();
  });

  it("stays idle and never fetches when chatId is missing", () => {
    const { result } = renderHook(() =>
      useAssetContent(null, "a1", { mode: "blob" }),
    );
    expect(result.current.status).toBe("idle");
    expect(api.getAssetContent).not.toHaveBeenCalled();
  });

  it("stays idle and never fetches when assetId is missing", () => {
    const { result } = renderHook(() =>
      useAssetContent("c1", null, { mode: "blob" }),
    );
    expect(result.current.status).toBe("idle");
    expect(api.getAssetContent).not.toHaveBeenCalled();
  });

  it("revokes the object URL on unmount (blob mode)", async () => {
    const { result, unmount } = renderHook(() =>
      useAssetContent("c1", "a1", { mode: "blob" }),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test-url");
  });

  it("does not revoke on unmount when the run produced no object URL (text mode)", async () => {
    api.getAssetContent.mockResolvedValue(blobResponse("plain"));
    const { result, unmount } = renderHook(() =>
      useAssetContent("c1", "a1", { mode: "text" }),
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    unmount();
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  });

  it("revokes a blob that resolves after unmount and skips the state update", async () => {
    const d = deferred();
    api.getAssetContent.mockReturnValue(d.promise);
    const { unmount } = renderHook(() =>
      useAssetContent("c1", "a1", { mode: "blob" }),
    );
    unmount();
    await act(async () => {
      d.resolve(blobResponse());
      await Promise.resolve();
      await Promise.resolve();
    });
    // Blob was created then immediately revoked because the run was cancelled.
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test-url");
  });

  it("ignores a text fetch that resolves after unmount", async () => {
    const d = deferred();
    api.getAssetContent.mockReturnValue(d.promise);
    const { result, unmount } = renderHook(() =>
      useAssetContent("c1", "a1", { mode: "text" }),
    );
    unmount();
    await act(async () => {
      d.resolve(blobResponse("late"));
      await Promise.resolve();
    });
    // No throw; the last observed state stayed loading (never became ready).
    expect(result.current.status).toBe("loading");
  });

  it("ignores a rejection that lands after unmount", async () => {
    const d = deferred();
    api.getAssetContent.mockReturnValue(d.promise);
    const { result, unmount } = renderHook(() =>
      useAssetContent("c1", "a1", { mode: "blob" }),
    );
    unmount();
    await act(async () => {
      d.reject({ status: 502 });
      await Promise.resolve();
    });
    expect(result.current.status).toBe("loading");
  });

  it("refetches when the asset id changes", async () => {
    const { result, rerender } = renderHook(
      ({ id }) => useAssetContent("c1", id, { mode: "text" }),
      { initialProps: { id: "a1" } },
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    api.getAssetContent.mockResolvedValue(blobResponse("second"));
    rerender({ id: "a2" });
    await waitFor(() => expect(result.current.text).toBe("second"));
    expect(api.getAssetContent).toHaveBeenCalledWith("c1", "a2");
  });
});
