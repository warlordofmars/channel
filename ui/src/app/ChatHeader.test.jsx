// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import ChatHeader from "./ChatHeader.jsx";

const mockChatsCtx = {
  renameChat: vi.fn().mockResolvedValue(undefined),
  archiveChat: vi.fn().mockResolvedValue(undefined),
  deleteChat: vi.fn().mockResolvedValue(undefined),
  renameChatLocal: vi.fn(),
};
vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: () => mockChatsCtx,
}));

function renderHeader(props) {
  return render(
    <MemoryRouter>
      <ChatHeader {...props} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockChatsCtx.renameChat.mockClear();
  mockChatsCtx.archiveChat.mockClear();
  mockChatsCtx.deleteChat.mockClear();
  mockChatsCtx.renameChatLocal.mockClear();
});

describe("ChatHeader", () => {
  it("renders empty strip when chat is null", () => {
    const { container } = renderHeader({ chat: null });
    expect(container.querySelector(".chat-hd")).toBeTruthy();
    expect(container.querySelector(".chat-hd-title")).toBeNull();
  });

  it("renders the title text when chat is provided", () => {
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    expect(screen.getByRole("button", { name: /alpha/i })).toBeTruthy();
  });

  it("click on title opens ChatRowMenu", () => {
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    fireEvent.click(screen.getByRole("button", { name: /alpha/i }));
    expect(screen.getByRole("menu")).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /rename/i })).toBeTruthy();
  });

  it("click Rename opens RenameChatModal with the chat's title", () => {
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    fireEvent.click(screen.getByRole("button", { name: /alpha/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /rename/i }));
    expect(screen.getByText(/rename chat/i)).toBeTruthy();
    const input = screen.getByLabelText(/title/i);
    expect(input.value).toBe("alpha");
  });

  it("click Delete opens DeleteChatModal", () => {
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    fireEvent.click(screen.getByRole("button", { name: /alpha/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
    expect(screen.getByText(/delete chat\?/i)).toBeTruthy();
  });

  it("swallows archiveChat rejection (no unhandled-promise-rejection)", async () => {
    mockChatsCtx.archiveChat.mockRejectedValueOnce(new Error("offline"));
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    fireEvent.click(screen.getByRole("button", { name: /alpha/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^archive$/i }));
    // Flush microtasks so the catch handler runs before the test ends.
    await Promise.resolve();
    expect(mockChatsCtx.archiveChat).toHaveBeenCalledWith("c1");
  });

  it("click Archive calls archiveChat and navigates to /app (header is always on the active chat)", () => {
    let pathname;
    function PathnameSpy() {
      pathname = useLocation().pathname;
      return null;
    }
    render(
      <MemoryRouter initialEntries={["/app/c/c1"]}>
        <Routes>
          <Route
            path="/app/*"
            element={
              <>
                <ChatHeader chat={{ chat_id: "c1", title: "alpha" }} />
                <PathnameSpy />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /alpha/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^archive$/i }));
    expect(mockChatsCtx.archiveChat).toHaveBeenCalledWith("c1");
    expect(pathname).toBe("/app");
  });

  it("share button is disabled placeholder", () => {
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    const share = screen.getByLabelText(/share/i);
    expect(share.disabled).toBe(true);
    expect(share.getAttribute("aria-disabled")).toBe("true");
  });

  it("RenameChatModal onClose clears renameOpen (modal closes)", () => {
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    fireEvent.click(screen.getByRole("button", { name: /alpha/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /rename/i }));
    expect(screen.getByText(/rename chat/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.queryByText(/rename chat/i)).toBeNull();
  });

  it("DeleteChatModal onClose clears deleteOpen (modal closes)", () => {
    renderHeader({ chat: { chat_id: "c1", title: "alpha" } });
    fireEvent.click(screen.getByRole("button", { name: /alpha/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
    expect(screen.getByText(/delete chat\?/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.queryByText(/delete chat\?/i)).toBeNull();
  });
});
