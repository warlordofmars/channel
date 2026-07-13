// Copyright (c) 2026 John Carter. All rights reserved.
import { act, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import UserDetail from "./UserDetail.jsx";
import { ApiError, getAdminUser } from "../../api.js";

vi.mock("../../api.js", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, getAdminUser: vi.fn() };
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

function makeDetail(overrides = {}) {
  return {
    user: {
      user_id: "amy@ex.com",
      email: "amy@ex.com",
      created_at: "2026-07-01T10:00:00.000000+00:00",
      last_login_at: null,
      chat_count: 2,
      last_chat_at: "2026-07-10T09:30:00.000000+00:00",
    },
    recent_chats: [
      {
        chat_id: "c1",
        title: "Quarterly plan",
        created_at: "2026-07-09T08:00:00.000000+00:00",
        last_message_at: "2026-07-10T09:30:00.000000+00:00",
        message_count: 12,
        archived: false,
      },
      {
        chat_id: "c2",
        title: null,
        created_at: "2026-07-02T08:00:00.000000+00:00",
        last_message_at: null,
        message_count: 0,
        archived: true,
      },
    ],
    recent_audit_events: [
      {
        event_id: "e1",
        event_type: "auth.login",
        created_at: "2026-07-10T09:00:00.123456+00:00",
        details: { ip: "10.0.0.1" },
      },
      {
        event_id: "e2",
        event_type: "auth.logout",
        created_at: null,
        details: null,
      },
    ],
    ...overrides,
  };
}

function renderDetail(path = "/app/admin/users/amy%40ex.com") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/admin/users/:id" element={<UserDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("UserDetail", () => {
  beforeEach(() => {
    getAdminUser.mockReset();
  });

  it("shows the loading state while the detail fetch is in flight", async () => {
    const d = deferred();
    getAdminUser.mockReturnValue(d.promise);
    renderDetail();
    expect(screen.getByText("Loading user…")).toBeTruthy();
    await act(async () => d.resolve(makeDetail()));
  });

  it("fetches with the decoded :id route param", async () => {
    getAdminUser.mockResolvedValue(makeDetail());
    await act(async () => renderDetail("/app/admin/users/amy%40ex.com"));
    expect(getAdminUser).toHaveBeenCalledWith("amy@ex.com");
  });

  it("renders the header row fields and all three sections", async () => {
    getAdminUser.mockResolvedValue(makeDetail());
    await act(async () => renderDetail());
    expect(
      screen.getByRole("heading", { level: 2, name: "amy@ex.com" }),
    ).toBeTruthy();
    // List-row fields: joined · last chat · chat count · last login (—).
    const head = screen.getByText(/Joined 2026-07-01/);
    expect(head.textContent).toContain("Last chat 2026-07-10");
    expect(head.textContent).toContain("2 chats");
    expect(head.textContent).toContain("Last login —");
    expect(screen.getByRole("heading", { level: 3, name: "Recent chats" })).toBeTruthy();
    expect(
      screen.getByRole("heading", { level: 3, name: "Recent audit events" }),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to users" }).getAttribute("href")).toBe(
      "/app/admin/users",
    );
  });

  it("links recent chats to /app/c/:id and renders null-field branches", async () => {
    getAdminUser.mockResolvedValue(makeDetail());
    await act(async () => renderDetail());
    const chatLink = screen.getByRole("link", { name: "Quarterly plan" });
    expect(chatLink.getAttribute("href")).toBe("/app/c/c1");
    // Null title → "Untitled"; archived flag → status column.
    expect(screen.getByRole("link", { name: "Untitled" }).getAttribute("href")).toBe(
      "/app/c/c2",
    );
    expect(screen.getByText("archived")).toBeTruthy();
    expect(screen.getByText("active")).toBeTruthy();
  });

  it("renders audit events with timestamps and optional details", async () => {
    getAdminUser.mockResolvedValue(makeDetail());
    await act(async () => renderDetail());
    expect(screen.getByText("auth.login")).toBeTruthy();
    expect(
      screen.getByText('2026-07-10 09:00:00 · {"ip":"10.0.0.1"}'),
    ).toBeTruthy();
    // Null created_at + null details → bare dash line.
    expect(screen.getByText("auth.logout")).toBeTruthy();
    expect(
      within(screen.getByTestId("admin-user-audit")).getByText("—"),
    ).toBeTruthy();
  });

  it("uses the singular form for a single chat", async () => {
    const detail = makeDetail();
    detail.user.chat_count = 1;
    getAdminUser.mockResolvedValue(detail);
    await act(async () => renderDetail());
    expect(screen.getByText(/1 chat ·/)).toBeTruthy();
  });

  it("renders the empty branches for chats and audit events", async () => {
    getAdminUser.mockResolvedValue(
      makeDetail({ recent_chats: [], recent_audit_events: [] }),
    );
    await act(async () => renderDetail());
    expect(screen.getByText("No chats yet.")).toBeTruthy();
    expect(screen.getByText("No audit events in the last 7 days.")).toBeTruthy();
  });

  it("renders the not-found state for a 404 (unknown user)", async () => {
    getAdminUser.mockRejectedValue(new ApiError("getAdminUser", 404, "User not found"));
    await act(async () => renderDetail("/app/admin/users/ghost%40ex.com"));
    expect(screen.getByRole("heading", { level: 2, name: "User not found" })).toBeTruthy();
    expect(screen.getByText("ghost@ex.com")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to users" }).getAttribute("href")).toBe(
      "/app/admin/users",
    );
  });

  it("renders the generic error state for non-404 failures", async () => {
    getAdminUser.mockRejectedValue(new Error("network down"));
    await act(async () => renderDetail());
    expect(
      screen.getByRole("heading", { level: 2, name: "Something went wrong" }),
    ).toBeTruthy();
    expect(screen.getByText(/could not load this user/i)).toBeTruthy();
  });

  it("treats a non-404 ApiError as a generic failure", async () => {
    getAdminUser.mockRejectedValue(new ApiError("getAdminUser", 500, null));
    await act(async () => renderDetail());
    expect(
      screen.getByRole("heading", { level: 2, name: "Something went wrong" }),
    ).toBeTruthy();
  });

  it("discards a response that lands after unmount", async () => {
    const d = deferred();
    getAdminUser.mockReturnValue(d.promise);
    const { unmount } = renderDetail();
    unmount();
    await act(async () => d.resolve(makeDetail()));
    expect(screen.queryByText("amy@ex.com")).toBeNull();
  });

  it("discards a failure that lands after unmount", async () => {
    const d = deferred();
    getAdminUser.mockReturnValue(d.promise);
    const { unmount } = renderDetail();
    unmount();
    await act(async () => d.reject(new ApiError("getAdminUser", 404, null)));
    expect(screen.queryByText("User not found")).toBeNull();
  });
});
