// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api.js", () => ({
  listMemoryRecords: vi.fn(),
}));

import * as api from "../../api.js";
import MemorySection from "./MemorySection.jsx";

// The live caps as `recall.py` ships them today. Every number the panel
// prints must come from here, never from a literal in the component.
const RECALL = {
  max_sessions: 5,
  events_per_session: 2,
  text_truncate: 120,
  ordering: "recency",
  enabled: true,
};

function record(over = {}) {
  return {
    record_id: "rec-1",
    kind: "conversation",
    role: "USER",
    text: "the stored text",
    created_at: "2026-07-01T10:00:00Z",
    used_in_recall: false,
    editable: false,
    ...over,
  };
}

function group(over = {}) {
  return {
    chat_id: "chat-1",
    chat_title: "Deploy notes",
    created_at: "2026-07-01",
    records: [record()],
    records_truncated: false,
    ...over,
  };
}

function page(over = {}) {
  return {
    groups: [],
    summaries: [],
    recall_window: RECALL,
    withheld_record_count: 0,
    next_cursor: null,
    ...over,
  };
}

function renderSection(entry = "/app/customize") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <MemorySection />
    </MemoryRouter>,
  );
}

// Settles the mount effect's already-resolved promise so assertions run
// against the ready state rather than the loading placeholder.
async function renderReady(entry) {
  const utils = renderSection(entry);
  await screen.findByText(/Channel stores everything below/);
  return utils;
}

describe("MemorySection", () => {
  beforeEach(() => {
    api.listMemoryRecords.mockReset();
    api.listMemoryRecords.mockResolvedValue(page());
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe("states", () => {
    it("shows a loading placeholder until the first page lands", async () => {
      let resolvePage;
      api.listMemoryRecords.mockReturnValueOnce(
        new Promise((resolve) => {
          resolvePage = resolve;
        }),
      );
      renderSection();
      expect(screen.getByText("Loading…")).toBeTruthy();

      await act(async () => {
        resolvePage(page());
      });
      expect(screen.queryByText("Loading…")).toBeNull();
    });

    it("surfaces a load failure as an alert", async () => {
      api.listMemoryRecords.mockRejectedValueOnce(new Error("boom"));
      renderSection();
      const alert = await screen.findByRole("alert");
      expect(alert.textContent).toContain("Couldn't load what Channel remembers");
    });

    it("reads as reassurance, not breakage, when nothing is stored", async () => {
      await renderReady();
      expect(screen.getByText(/Nothing stored yet/)).toBeTruthy();
    });

    it("requests the first page with the chats-per-page limit", async () => {
      await renderReady();
      expect(api.listMemoryRecords).toHaveBeenCalledWith({ limit: 10, chatId: null });
    });
  });

  describe("the stored-vs-used sentence", () => {
    it("is built from the recall_window envelope, not hardcoded numbers", async () => {
      await renderReady();
      const lead = screen.getByText(/Channel stores everything below/);
      expect(lead.textContent).toContain("up to 2 turns");
      expect(lead.textContent).toContain("5 most recent chats");
      expect(lead.textContent).toContain("120 characters");
      expect(lead.textContent).toContain("ordered by recency, not by relevance");
    });

    it("tracks the caps when they move, including down to singulars", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({
          recall_window: { ...RECALL, max_sessions: 1, events_per_session: 1 },
        }),
      );
      await renderReady();
      const lead = screen.getByText(/Channel stores everything below/);
      expect(lead.textContent).toContain("up to 1 turn from each of your 1 most recent chat,");
    });

    it("says nothing is being recalled when the hook is switched off", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({ recall_window: { ...RECALL, enabled: false } }),
      );
      await renderReady();
      expect(
        screen.getByText(/Recall is switched off right now/),
      ).toBeTruthy();
    });
  });

  describe("records", () => {
    it("groups by chat with kind, role, timestamp and the full text", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({
          groups: [
            group({
              records: [
                record({
                  record_id: "rec-a",
                  kind: "remembered",
                  role: "ASSISTANT",
                  text: "[remember] user prefers dark mode",
                  used_in_recall: true,
                }),
              ],
            }),
          ],
        }),
      );
      const { container } = await renderReady();

      const section = screen.getByLabelText("Deploy notes");
      expect(within(section).getByText("Remembered")).toBeTruthy();
      expect(within(section).getByText("Channel")).toBeTruthy();
      expect(within(section).getByText("[remember] user prefers dark mode")).toBeTruthy();
      expect(within(section).getByText("Used in recall")).toBeTruthy();
      expect(container.querySelector(".mem-time").textContent).not.toBe("");
    });

    it("shows records outside the recall window without a badge", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(page({ groups: [group()] }));
      await renderReady();
      expect(screen.getByText("the stored text")).toBeTruthy();
      expect(screen.queryByText("Used in recall")).toBeNull();
      expect(screen.getByText("From the conversation")).toBeTruthy();
      expect(screen.getByText("You")).toBeTruthy();
    });

    it("falls back to the chat id when the chat has no title", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({ groups: [group({ chat_title: null })] }),
      );
      await renderReady();
      expect(screen.getByLabelText("chat-1")).toBeTruthy();
    });

    it("shows an unrecognised kind or role verbatim rather than hiding it", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({
          groups: [group({ records: [record({ kind: "future-kind", role: "TOOL" })] })],
        }),
      );
      await renderReady();
      expect(screen.getByText("future-kind")).toBeTruthy();
      expect(screen.getByText("TOOL")).toBeTruthy();
    });

    it("reports a chat whose oldest records are past the per-page cap", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({ groups: [group({ records_truncated: true })] }),
      );
      await renderReady();
      expect(screen.getByText(/its oldest aren't listed/)).toBeTruthy();
    });

    it("flags withheld records as the read-boundary canary", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({ groups: [group()], withheld_record_count: 1 }),
      );
      await renderReady();
      const note = screen.getByRole("status");
      // Singular — the count is a lower bound, so the wording has to work at 1.
      expect(note.textContent).toContain("At least 1 stored record");
    });
  });

  describe("plain-text rendering (#465, epic #129 decision 11)", () => {
    it("renders forged Markdown structure as literal text, never as a heading", async () => {
      const forged = "a stored note\n\n## Fake heading\n\n**not bold**";
      api.listMemoryRecords.mockResolvedValueOnce(
        page({ groups: [group({ records: [record({ text: forged })] })] }),
      );
      const { container } = await renderReady();

      const body = container.querySelector(".mem-text");
      expect(body.textContent).toBe(forged);
      // No element children at all: the bytes went in as one text node.
      expect(body.children).toHaveLength(0);
      expect(body.querySelector("h1, h2, h3, h4, h5, h6, strong, em")).toBeNull();
    });
  });

  describe("summaries", () => {
    it("renders head summaries as a distinct read-only group", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({
          summaries: [
            {
              chat_id: "chat-9",
              chat_title: "Long thread",
              text: "They discussed deployment.",
              covers_through: "MSG#2026-07-01#m1",
              updated_at: "2026-07-02T09:00:00Z",
            },
          ],
        }),
      );
      await renderReady();

      const section = screen.getByLabelText("Chat summaries");
      expect(within(section).getByText(/Read-only/)).toBeTruthy();
      expect(within(section).getByText(/removed when the chat is deleted/)).toBeTruthy();
      expect(within(section).getByText("They discussed deployment.")).toBeTruthy();
      expect(within(section).getByText("Long thread")).toBeTruthy();
    });

    it("falls back to the chat id for an untitled summarised chat", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({
          summaries: [
            {
              chat_id: "chat-9",
              chat_title: null,
              text: "gist",
              covers_through: "MSG#2026-07-01#m1",
              updated_at: "2026-07-02T09:00:00Z",
            },
          ],
        }),
      );
      await renderReady();
      expect(screen.getByText("chat-9")).toBeTruthy();
    });
  });

  describe("kind filter", () => {
    it("narrows to one kind and drops chats left with nothing to show", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({
          groups: [
            group({
              records: [
                record({ record_id: "r-conv" }),
                record({ record_id: "r-mem", kind: "remembered", text: "[remember] x" }),
              ],
            }),
            group({ chat_id: "chat-2", chat_title: "Only chatter" }),
          ],
        }),
      );
      await renderReady();

      fireEvent.click(screen.getByRole("button", { name: "Remembered" }));

      expect(screen.getByText("[remember] x")).toBeTruthy();
      expect(screen.queryByText("the stored text")).toBeNull();
      expect(screen.queryByLabelText("Only chatter")).toBeNull();
    });

    it("explains an empty result from the filter without claiming nothing is stored", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(page({ groups: [group()] }));
      await renderReady();

      fireEvent.click(screen.getByRole("button", { name: "Tool notes" }));

      expect(screen.getByText("No stored records of this kind.")).toBeTruthy();
      expect(screen.queryByText(/Nothing stored yet/)).toBeNull();
    });

    it("marks the active chip pressed", async () => {
      await renderReady();
      expect(screen.getByRole("button", { name: "All" }).getAttribute("aria-pressed")).toBe(
        "true",
      );
      fireEvent.click(screen.getByRole("button", { name: "Conversation" }));
      expect(
        screen.getByRole("button", { name: "Conversation" }).getAttribute("aria-pressed"),
      ).toBe("true");
      expect(screen.getByRole("button", { name: "All" }).getAttribute("aria-pressed")).toBe(
        "false",
      );
    });
  });

  describe("chat filter", () => {
    it("scopes the request to the ?chat_id= search param", async () => {
      await renderReady("/app/customize?chat_id=chat-7");
      expect(api.listMemoryRecords).toHaveBeenCalledWith({ limit: 10, chatId: "chat-7" });
    });

    it("clears the scope and refetches every chat", async () => {
      await renderReady("/app/customize?chat_id=chat-7");
      fireEvent.click(screen.getByRole("button", { name: /Showing one chat/ }));

      await waitFor(() =>
        expect(api.listMemoryRecords).toHaveBeenLastCalledWith({ limit: 10, chatId: null }),
      );
      expect(screen.queryByRole("button", { name: /Showing one chat/ })).toBeNull();
    });

    it("offers no clear control when no chat scope is set", async () => {
      await renderReady();
      expect(screen.queryByRole("button", { name: /Showing one chat/ })).toBeNull();
    });
  });

  describe("pagination", () => {
    it("pages on next_cursor and appends both groups and summaries", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({ groups: [group()], next_cursor: "cur-1" }),
      );
      await renderReady();

      api.listMemoryRecords.mockResolvedValueOnce(
        page({
          groups: [group({ chat_id: "chat-2", chat_title: "Older chat" })],
          summaries: [
            {
              chat_id: "chat-2",
              chat_title: "Older chat",
              text: "older gist",
              covers_through: "MSG#2026-06-01#m1",
              updated_at: "2026-06-02T09:00:00Z",
            },
          ],
        }),
      );
      fireEvent.click(screen.getByRole("button", { name: "Load more" }));

      await waitFor(() => expect(screen.getByLabelText("Older chat")).toBeTruthy());
      expect(api.listMemoryRecords).toHaveBeenLastCalledWith({
        limit: 10,
        cursor: "cur-1",
        chatId: null,
      });
      expect(screen.getByLabelText("Deploy notes")).toBeTruthy();
      expect(screen.getByText("older gist")).toBeTruthy();
      // Exhausted cursor retires the button.
      expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    });

    it("disables the button while a page is in flight", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({ groups: [group()], next_cursor: "cur-1" }),
      );
      await renderReady();

      let resolvePage;
      api.listMemoryRecords.mockReturnValueOnce(
        new Promise((resolve) => {
          resolvePage = resolve;
        }),
      );
      fireEvent.click(screen.getByRole("button", { name: "Load more" }));
      expect(screen.getByRole("button", { name: "Loading…" }).disabled).toBe(true);

      await act(async () => {
        resolvePage(page({ next_cursor: "cur-2" }));
      });
      expect(screen.getByRole("button", { name: "Load more" }).disabled).toBe(false);
    });

    it("keeps the list and the button when a page fails", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({ groups: [group()], next_cursor: "cur-1" }),
      );
      await renderReady();

      api.listMemoryRecords.mockRejectedValueOnce(new Error("nope"));
      fireEvent.click(screen.getByRole("button", { name: "Load more" }));

      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Load more" }).disabled).toBe(false),
      );
      expect(screen.getByLabelText("Deploy notes")).toBeTruthy();
    });

    it("offers no button when the first page already exhausted the cursor", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(page({ groups: [group()] }));
      await renderReady();
      expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    });
  });

  describe("unmount safety", () => {
    it("drops a first page that resolves after the view is gone", async () => {
      let resolvePage;
      api.listMemoryRecords.mockReturnValueOnce(
        new Promise((resolve) => {
          resolvePage = resolve;
        }),
      );
      const { unmount } = renderSection();
      unmount();

      await act(async () => {
        resolvePage(page({ groups: [group()] }));
      });
      expect(screen.queryByLabelText("Deploy notes")).toBeNull();
    });

    it("drops a first-page failure that lands after the view is gone", async () => {
      let rejectPage;
      api.listMemoryRecords.mockReturnValueOnce(
        new Promise((resolve, reject) => {
          rejectPage = reject;
        }),
      );
      const { unmount } = renderSection();
      unmount();

      await act(async () => {
        rejectPage(new Error("late"));
      });
      expect(screen.queryByRole("alert")).toBeNull();
    });

    it("drops a load-more page for a chat scope the user already cleared", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({ groups: [group({ chat_title: "Scoped chat" })], next_cursor: "cur-1" }),
      );
      await renderReady("/app/customize?chat_id=chat-1");

      // Page 2 of the scoped list is still in flight…
      let resolveStale;
      api.listMemoryRecords.mockReturnValueOnce(
        new Promise((resolve) => {
          resolveStale = resolve;
        }),
      );
      fireEvent.click(screen.getByRole("button", { name: "Load more" }));

      // …when the user drops the scope, which reloads page 1 for every chat.
      api.listMemoryRecords.mockResolvedValueOnce(
        page({ groups: [group({ chat_id: "chat-2", chat_title: "Every chat" })] }),
      );
      fireEvent.click(screen.getByRole("button", { name: /Showing one chat/ }));
      await waitFor(() => expect(screen.getByLabelText("Every chat")).toBeTruthy());

      await act(async () => {
        resolveStale(page({ groups: [group({ chat_id: "chat-3", chat_title: "Stale page" })] }));
      });

      // The stale page belongs to a list that no longer exists.
      expect(screen.queryByLabelText("Stale page")).toBeNull();
      expect(screen.getByLabelText("Every chat")).toBeTruthy();
      // …and its settle must not resurrect a "Load more" the new page retired.
      expect(screen.queryByRole("button", { name: /Load more|Loading…/ })).toBeNull();
    });

    it("drops a load-more page that lands after the view is gone", async () => {
      api.listMemoryRecords.mockResolvedValueOnce(
        page({ groups: [group()], next_cursor: "cur-1" }),
      );
      const { unmount } = await renderReady();

      let resolvePage;
      api.listMemoryRecords.mockReturnValueOnce(
        new Promise((resolve) => {
          resolvePage = resolve;
        }),
      );
      fireEvent.click(screen.getByRole("button", { name: "Load more" }));
      unmount();

      await act(async () => {
        resolvePage(page({ groups: [group({ chat_id: "chat-2", chat_title: "Older chat" })] }));
      });
      expect(screen.queryByLabelText("Older chat")).toBeNull();
    });
  });
});
