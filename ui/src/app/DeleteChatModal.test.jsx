// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import DeleteChatModal from "./DeleteChatModal.jsx";

const deleteChatMock = vi.fn();
const navigateMock = vi.fn();

vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: () => ({ deleteChat: deleteChatMock }),
}));
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return { ...actual, useNavigate: () => navigateMock };
});

function renderModal(props) {
  return render(
    <MemoryRouter>
      <DeleteChatModal {...props} />
    </MemoryRouter>,
  );
}

describe("DeleteChatModal", () => {
  beforeEach(() => {
    deleteChatMock.mockReset();
    deleteChatMock.mockResolvedValue(undefined);
    navigateMock.mockReset();
  });

  it("renders nothing when open=false", () => {
    const { container } = renderModal({
      open: false, chatId: "c1", chatTitle: "alpha", isActive: false, onClose: () => {},
    });
    expect(container.firstChild).toBeNull();
  });

  it("renders title + warning text + danger-styled Delete button", () => {
    renderModal({ open: true, chatId: "c1", chatTitle: "alpha", isActive: false, onClose: () => {} });
    expect(screen.getByText(/delete chat\?/i)).toBeTruthy();
    expect(screen.getByText(/alpha/)).toBeTruthy();
    expect(screen.getByText(/cannot be undone/i)).toBeTruthy();
    const del = screen.getByRole("button", { name: /^delete$/i });
    expect(del.classList.contains("btn-danger")).toBe(true);
  });

  it("Cancel closes without deleting", () => {
    const onClose = vi.fn();
    renderModal({ open: true, chatId: "c1", chatTitle: "a", isActive: false, onClose });
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(deleteChatMock).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("Delete calls deleteChat(chatId) and closes", async () => {
    const onClose = vi.fn();
    renderModal({ open: true, chatId: "c1", chatTitle: "a", isActive: false, onClose });
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(deleteChatMock).toHaveBeenCalledWith("c1"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("navigates to /app BEFORE deleting when isActive=true", async () => {
    renderModal({ open: true, chatId: "c1", chatTitle: "a", isActive: true, onClose: () => {} });
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/app"));
    await waitFor(() => expect(deleteChatMock).toHaveBeenCalledWith("c1"));
    expect(navigateMock.mock.invocationCallOrder[0])
      .toBeLessThan(deleteChatMock.mock.invocationCallOrder[0]);
  });

  it("default focus is on Cancel, not Delete", async () => {
    renderModal({ open: true, chatId: "c1", chatTitle: "a", isActive: false, onClose: () => {} });
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: /cancel/i })),
    );
  });
});
