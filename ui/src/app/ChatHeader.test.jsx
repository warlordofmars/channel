// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import ChatHeader from "./ChatHeader.jsx";

vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: () => ({
    renameChat: vi.fn(),
    deleteChat: vi.fn(),
    renameChatLocal: vi.fn(),
  }),
}));

function renderHeader(props) {
  return render(
    <MemoryRouter>
      <ChatHeader {...props} />
    </MemoryRouter>,
  );
}

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
