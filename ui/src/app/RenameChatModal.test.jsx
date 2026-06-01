// Copyright (c) 2026 John Carter. All rights reserved.
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import RenameChatModal from "./RenameChatModal.jsx";

const renameChatMock = vi.fn();
vi.mock("../hooks/ChatsContext.jsx", () => ({
  useChats: () => ({ renameChat: renameChatMock }),
}));

describe("RenameChatModal", () => {
  beforeEach(() => {
    renameChatMock.mockReset();
    renameChatMock.mockResolvedValue(undefined);
  });

  it("renders nothing when open=false", () => {
    const { container } = render(
      <RenameChatModal open={false} chatId="c1" currentTitle="t" onClose={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("pre-fills + pre-selects the input", async () => {
    render(
      <RenameChatModal open chatId="c1" currentTitle="alpha" onClose={() => {}} />,
    );
    const input = screen.getByLabelText(/title/i);
    await waitFor(() => {
      expect(input.value).toBe("alpha");
      expect(input.selectionStart).toBe(0);
      expect(input.selectionEnd).toBe("alpha".length);
    });
  });

  it("Save is disabled when input is empty or unchanged", () => {
    render(
      <RenameChatModal open chatId="c1" currentTitle="alpha" onClose={() => {}} />,
    );
    const save = screen.getByRole("button", { name: /save/i });
    expect(save.disabled).toBe(true);  // unchanged
    const input = screen.getByLabelText(/title/i);
    fireEvent.change(input, { target: { value: "" } });
    expect(save.disabled).toBe(true);  // empty
    fireEvent.change(input, { target: { value: "beta" } });
    expect(save.disabled).toBe(false); // changed + non-empty
  });

  it("Save calls renameChat with chatId + trimmed new title and closes", async () => {
    const onClose = vi.fn();
    render(
      <RenameChatModal open chatId="c1" currentTitle="alpha" onClose={onClose} />,
    );
    const input = screen.getByLabelText(/title/i);
    fireEvent.change(input, { target: { value: "  beta  " } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(renameChatMock).toHaveBeenCalledWith("c1", "beta"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("Enter submits when valid", async () => {
    render(
      <RenameChatModal open chatId="c1" currentTitle="alpha" onClose={() => {}} />,
    );
    const input = screen.getByLabelText(/title/i);
    fireEvent.change(input, { target: { value: "beta" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(renameChatMock).toHaveBeenCalledWith("c1", "beta"));
  });

  it("Cancel button closes without calling renameChat", () => {
    const onClose = vi.fn();
    render(
      <RenameChatModal open chatId="c1" currentTitle="alpha" onClose={onClose} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(renameChatMock).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("shows inline error on API failure and stays open", async () => {
    renameChatMock.mockRejectedValueOnce(new Error("nope"));
    const onClose = vi.fn();
    render(
      <RenameChatModal open chatId="c1" currentTitle="alpha" onClose={onClose} />,
    );
    fireEvent.change(screen.getByLabelText(/title/i), { target: { value: "beta" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(screen.getByText(/couldn't rename/i)).toBeTruthy());
    expect(onClose).not.toHaveBeenCalled();
  });
});
