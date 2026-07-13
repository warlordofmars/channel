// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Users, { fmtDate } from "./Users.jsx";
import { ApiError, getAdminUsers } from "../../api.js";

vi.mock("../../api.js", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, getAdminUsers: vi.fn() };
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeUser(overrides = {}) {
  return {
    user_id: "amy@ex.com",
    email: "amy@ex.com",
    created_at: "2026-07-01T10:00:00.000000+00:00",
    last_login_at: null,
    chat_count: 5,
    last_chat_at: "2026-07-10T09:30:00.000000+00:00",
    ...overrides,
  };
}

function renderUsers() {
  return render(
    <MemoryRouter initialEntries={["/app/admin/users"]}>
      <Users />
    </MemoryRouter>,
  );
}

describe("Users", () => {
  beforeEach(() => {
    getAdminUsers.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the loading state while the first page is in flight", async () => {
    const d = deferred();
    getAdminUsers.mockReturnValue(d.promise);
    renderUsers();
    expect(screen.getByText("Loading users…")).toBeTruthy();
    await act(async () => d.resolve({ items: [], next_cursor: null }));
  });

  it("renders a row per user, dashes for null fields, and detail links", async () => {
    getAdminUsers.mockResolvedValue({
      items: [
        makeUser(),
        makeUser({
          user_id: "bob@ex.com",
          email: "bob@ex.com",
          created_at: null,
          last_chat_at: null,
          chat_count: 0,
        }),
      ],
      next_cursor: null,
    });
    await act(async () => renderUsers());
    expect(screen.getByTestId("admin-users-table")).toBeTruthy();
    expect(screen.getByText("2026-07-01")).toBeTruthy(); // joined
    expect(screen.getByText("2026-07-10")).toBeTruthy(); // last chat
    // bob: created_at + last_chat_at null, both users' last_login null.
    expect(screen.getAllByText("—")).toHaveLength(4);
    const link = screen.getByRole("link", { name: "amy@ex.com" });
    expect(link.getAttribute("href")).toBe("/app/admin/users/amy%40ex.com");
    expect(screen.queryByRole("button", { name: /load more/i })).toBeNull();
  });

  it("requests the server default sort and marks Last chat descending", async () => {
    getAdminUsers.mockResolvedValue({ items: [makeUser()], next_cursor: null });
    await act(async () => renderUsers());
    expect(getAdminUsers).toHaveBeenCalledWith({ sort: "last_chat_at" });
    // aria-sort lives on the <th> — descending for the active timestamp
    // column, "none" on the inactive sortable columns.
    const active = screen.getByRole("columnheader", { name: /last chat/i });
    expect(active.getAttribute("aria-sort")).toBe("descending");
    const inactive = screen.getByRole("columnheader", { name: /email/i });
    expect(inactive.getAttribute("aria-sort")).toBe("none");
    const unsortable = screen.getByRole("columnheader", { name: "Chats" });
    expect(unsortable.getAttribute("aria-sort")).toBeNull();
    // Timestamps sort descending → indicator arrow points down.
    expect(screen.getByTestId("sort-indicator").style.transform).toBe(
      "rotate(180deg)",
    );
  });

  it("clicking another header re-queries under that sort from page 1", async () => {
    getAdminUsers.mockResolvedValueOnce({
      items: [makeUser()],
      next_cursor: "cur-1",
    });
    getAdminUsers.mockResolvedValueOnce({
      items: [makeUser({ user_id: "zoe@ex.com", email: "zoe@ex.com" })],
      next_cursor: null,
    });
    await act(async () => renderUsers());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /email/i }));
    });
    expect(getAdminUsers).toHaveBeenLastCalledWith({ sort: "email" });
    // Pagination reset: the new page replaces the old rows.
    expect(screen.getByText("zoe@ex.com")).toBeTruthy();
    expect(screen.queryByText("amy@ex.com")).toBeNull();
    // Email sorts ascending → indicator is not rotated, th announces it.
    expect(screen.getByTestId("sort-indicator").style.transform).toBe("none");
    expect(
      screen.getByRole("columnheader", { name: /email/i }).getAttribute("aria-sort"),
    ).toBe("ascending");
  });

  it("clicking the active sort header is a no-op", async () => {
    getAdminUsers.mockResolvedValue({ items: [makeUser()], next_cursor: null });
    await act(async () => renderUsers());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /last chat/i }));
    });
    expect(getAdminUsers).toHaveBeenCalledTimes(1);
  });

  it("Load more pages on next_cursor and appends the next page", async () => {
    getAdminUsers.mockResolvedValueOnce({
      items: [makeUser()],
      next_cursor: "cur-1",
    });
    const d = deferred();
    getAdminUsers.mockReturnValueOnce(d.promise);
    await act(async () => renderUsers());
    const button = screen.getByRole("button", { name: "Load more" });
    fireEvent.click(button);
    // In flight: disabled + label swap.
    expect(screen.getByRole("button", { name: "Loading…" }).disabled).toBe(true);
    await act(async () =>
      d.resolve({
        items: [makeUser({ user_id: "bob@ex.com", email: "bob@ex.com" })],
        next_cursor: null,
      }),
    );
    expect(getAdminUsers).toHaveBeenLastCalledWith({
      sort: "last_chat_at",
      cursor: "cur-1",
    });
    expect(screen.getByText("amy@ex.com")).toBeTruthy();
    expect(screen.getByText("bob@ex.com")).toBeTruthy();
    // next_cursor null = exhausted → button gone.
    expect(screen.queryByRole("button", { name: /load more/i })).toBeNull();
  });

  it("ignores sort clicks while a Load more append is in flight", async () => {
    getAdminUsers.mockResolvedValueOnce({
      items: [makeUser()],
      next_cursor: "cur-1",
    });
    const d = deferred();
    getAdminUsers.mockReturnValueOnce(d.promise);
    await act(async () => renderUsers());
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    // Sort switch mid-append would reset rows to null and let the
    // stale append spread null / corrupt the new listing — the click
    // must be a no-op until the append settles.
    fireEvent.click(screen.getByRole("button", { name: /email/i }));
    expect(getAdminUsers).toHaveBeenCalledTimes(2); // no third (sort) fetch
    await act(async () =>
      d.resolve({
        items: [makeUser({ user_id: "bob@ex.com", email: "bob@ex.com" })],
        next_cursor: null,
      }),
    );
    // Append landed under the original sort; both pages render.
    expect(screen.getByText("amy@ex.com")).toBeTruthy();
    expect(screen.getByText("bob@ex.com")).toBeTruthy();
    // Once settled, sort clicks work again.
    getAdminUsers.mockResolvedValueOnce({ items: [], next_cursor: null });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /email/i }));
    });
    expect(getAdminUsers).toHaveBeenLastCalledWith({ sort: "email" });
  });

  it("a 400 on Load more restarts from page 1 (stale cursor contract)", async () => {
    getAdminUsers.mockResolvedValueOnce({
      items: [makeUser()],
      next_cursor: "cur-stale",
    });
    getAdminUsers.mockRejectedValueOnce(
      new ApiError("getAdminUsers", 400, "invalid cursor"),
    );
    getAdminUsers.mockResolvedValueOnce({
      items: [makeUser({ user_id: "new@ex.com", email: "new@ex.com" })],
      next_cursor: null,
    });
    await act(async () => renderUsers());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    });
    // Third call is a fresh page-1 fetch (no cursor).
    expect(getAdminUsers).toHaveBeenCalledTimes(3);
    expect(getAdminUsers).toHaveBeenLastCalledWith({ sort: "last_chat_at" });
    expect(screen.getByText("new@ex.com")).toBeTruthy();
    expect(screen.queryByText(/could not load/i)).toBeNull();
  });

  it("a non-400 Load more failure keeps rows and clears on retry", async () => {
    getAdminUsers.mockResolvedValueOnce({
      items: [makeUser()],
      next_cursor: "cur-1",
    });
    getAdminUsers.mockRejectedValueOnce(new Error("network down"));
    getAdminUsers.mockResolvedValueOnce({
      items: [makeUser({ user_id: "bob@ex.com", email: "bob@ex.com" })],
      next_cursor: null,
    });
    await act(async () => renderUsers());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    });
    expect(screen.getByText("Could not load more users.")).toBeTruthy();
    expect(screen.getByText("amy@ex.com")).toBeTruthy(); // rows kept
    // The button stays — clicking again retries and clears the error.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    });
    expect(screen.queryByText("Could not load more users.")).toBeNull();
    expect(screen.getByText("bob@ex.com")).toBeTruthy();
  });

  it("renders the empty state when there are no users", async () => {
    getAdminUsers.mockResolvedValue({ items: [], next_cursor: null });
    await act(async () => renderUsers());
    expect(screen.getByText("No users yet.")).toBeTruthy();
  });

  it("initial load failure shows the error state and Retry refetches", async () => {
    getAdminUsers.mockRejectedValueOnce(new Error("boom"));
    getAdminUsers.mockResolvedValueOnce({
      items: [makeUser()],
      next_cursor: null,
    });
    await act(async () => renderUsers());
    expect(screen.getByText("Could not load users.")).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    });
    expect(getAdminUsers).toHaveBeenCalledTimes(2);
    expect(screen.getByText("amy@ex.com")).toBeTruthy();
  });

  it("discards a first-page response that lands after unmount", async () => {
    const d = deferred();
    getAdminUsers.mockReturnValue(d.promise);
    const { unmount } = renderUsers();
    unmount();
    await act(async () => d.resolve({ items: [makeUser()], next_cursor: null }));
    expect(screen.queryByText("amy@ex.com")).toBeNull();
  });

  it("discards a first-page failure that lands after unmount", async () => {
    const d = deferred();
    getAdminUsers.mockReturnValue(d.promise);
    const { unmount } = renderUsers();
    unmount();
    await act(async () => d.reject(new Error("late failure")));
    expect(screen.queryByText("Could not load users.")).toBeNull();
  });

  it("fmtDate slices the date part and dashes null", () => {
    expect(fmtDate("2026-07-01T10:00:00.000000+00:00")).toBe("2026-07-01");
    expect(fmtDate(null)).toBe("—");
  });
});
