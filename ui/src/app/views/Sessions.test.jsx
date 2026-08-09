// Copyright (c) 2026 John Carter. All rights reserved.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api.js", () => ({
  endSession: vi.fn(),
  listSessions: vi.fn(),
  revokeAllSessions: vi.fn(),
  revokeSession: vi.fn(),
}));

import * as api from "../../api.js";
import Sessions, { deviceLabel } from "./Sessions.jsx";

// A session as `GET /api/me/sessions` projects it — every field the server's
// `Session` model carries, so a test that leans on one is leaning on the real
// shape rather than a convenient subset.
function session(over = {}) {
  return {
    device_id: "5f2a9c31-0d44-4b7e-9a11-2c3d4e5f6071",
    issued_at: "2026-08-01T09:00:00Z",
    last_used_at: "2026-08-01T11:00:00Z",
    absolute_expires_at: "2026-08-31T09:00:00Z",
    idle_expires_at: "2026-08-08T11:00:00Z",
    ...over,
  };
}

// A promise whose settlement the test drives, for the mid-flight cases
// (unmount during a request, the disabled-while-busy window).
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function renderReady(rows = [session()]) {
  api.listSessions.mockResolvedValue(rows);
  const view = render(<Sessions />);
  await screen.findByText("Everywhere");
  return view;
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("deviceLabel", () => {
  it("shortens an opaque device id to a stable handle", () => {
    expect(deviceLabel("5f2a9c31-0d44-4b7e")).toBe("Device 5f2a9c31");
  });

  it("drops non-alphanumerics so the handle can't carry structure", () => {
    expect(deviceLabel("ab-cd_ef!gh~ij")).toBe("Device abcdefgh");
  });

  it("falls back when the id has nothing renderable", () => {
    expect(deviceLabel("---")).toBe("Unnamed device");
  });

  it("tolerates a missing id", () => {
    expect(deviceLabel(undefined)).toBe("Unnamed device");
  });
});

describe("Sessions — loading and error", () => {
  it("shows a loading state before the list resolves", async () => {
    const gate = deferred();
    api.listSessions.mockReturnValue(gate.promise);
    render(<Sessions />);

    expect(screen.getByText("Loading…")).toBeTruthy();

    await act(async () => {
      gate.resolve([]);
    });
  });

  it("reports a failed load without pretending the list is empty", async () => {
    api.listSessions.mockRejectedValue(new Error("boom"));
    render(<Sessions />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't load your signed-in devices");
    // The empty-state copy makes a claim about the account, so it must not
    // appear when the truth is simply that nothing loaded.
    expect(screen.queryByText(/No other signed-in devices/)).toBeNull();
  });

  it("drops a list that resolves after the view unmounts", async () => {
    const gate = deferred();
    api.listSessions.mockReturnValue(gate.promise);
    const { unmount } = render(<Sessions />);
    unmount();

    // No "state update on an unmounted component" warning, and no throw.
    await act(async () => {
      gate.resolve([session()]);
    });
  });

  it("drops a load FAILURE that lands after the view unmounts", async () => {
    const gate = deferred();
    api.listSessions.mockReturnValue(gate.promise);
    const { unmount } = render(<Sessions />);
    unmount();

    await act(async () => {
      gate.reject(new Error("late"));
    });
  });
});

describe("Sessions — the list", () => {
  it("renders one row per device with its label and last-used time", async () => {
    await renderReady([
      session({ device_id: "aaaa1111-bbbb", last_used_at: "2026-08-01T11:00:00Z" }),
      session({ device_id: "cccc2222-dddd", last_used_at: "2026-08-01T11:00:00Z" }),
    ]);

    expect(screen.getByText("Device aaaa1111")).toBeTruthy();
    expect(screen.getByText("Device cccc2222")).toBeTruthy();
    // Both halves of the meta line, joined.
    const meta = screen.getAllByText(/Last used .* · signed in .*/);
    expect(meta.length).toBe(2);
  });

  it("renders the sign-in clause alone when there is no last-used stamp", async () => {
    await renderReady([session({ last_used_at: null })]);
    expect(screen.getByText(/^signed in .*$/)).toBeTruthy();
  });

  it("renders the last-used clause alone when there is no sign-in stamp", async () => {
    await renderReady([session({ issued_at: "" })]);
    expect(screen.getByText(/^Last used .*$/)).toBeTruthy();
  });

  it("says so rather than rendering an empty meta line", async () => {
    await renderReady([session({ issued_at: null, last_used_at: null })]);
    expect(screen.getByText("No activity recorded")).toBeTruthy();
  });

  it("shows the empty state when no sessions are tracked", async () => {
    await renderReady([]);
    expect(screen.getByText(/No other signed-in devices are being tracked/)).toBeTruthy();
    // The "sign out everywhere" control stays available: the list being empty
    // is an index/bypass artefact, not proof there is nothing to revoke.
    expect(screen.getByRole("button", { name: "Sign out everywhere" })).toBeTruthy();
  });
});

describe("Sessions — per-device revoke", () => {
  it("confirms before revoking, and names the device in the dialog", async () => {
    await renderReady([session({ device_id: "aaaa1111-bbbb" })]);

    fireEvent.click(screen.getByRole("button", { name: "Sign out Device aaaa1111" }));

    expect(screen.getByText("Sign out this device?")).toBeTruthy();
    expect(screen.getByText(/Device aaaa1111 will be signed out/)).toBeTruthy();
    // Nothing has been revoked yet — the confirm is the gate.
    expect(api.revokeSession).not.toHaveBeenCalled();
  });

  it("revokes the device and re-reads the list", async () => {
    await renderReady([session({ device_id: "aaaa1111-bbbb" })]);
    api.revokeSession.mockResolvedValue(undefined);
    api.listSessions.mockResolvedValue([]);

    fireEvent.click(screen.getByRole("button", { name: "Sign out Device aaaa1111" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    });

    expect(api.revokeSession).toHaveBeenCalledWith("aaaa1111-bbbb");
    await screen.findByText("Device aaaa1111 signed out.");
    // The refreshed list is what's displayed — never a client-side count of
    // what was revoked, which the eventually-consistent index can't promise.
    expect(screen.getByText(/No other signed-in devices are being tracked/)).toBeTruthy();
    expect(screen.queryByText(/revoked/)).toBeNull();
  });

  it("treats a 404 as already-gone, not as a failure", async () => {
    // The index-propagation window: the list showed a row the revoke can no
    // longer find. The user's goal is met, so this is a success with a note.
    await renderReady([session({ device_id: "aaaa1111-bbbb" })]);
    const gone = Object.assign(new Error("revokeSession failed: 404"), { status: 404 });
    api.revokeSession.mockRejectedValue(gone);
    api.listSessions.mockResolvedValue([]);

    fireEvent.click(screen.getByRole("button", { name: "Sign out Device aaaa1111" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    });

    await screen.findByText("Device aaaa1111 was already signed out.");
    expect(screen.queryByRole("alert")).toBeNull();
    // The list was still refreshed — the whole point of not erroring out.
    expect(api.listSessions).toHaveBeenCalledTimes(2);
  });

  it("surfaces a non-404 failure and keeps the row", async () => {
    await renderReady([session({ device_id: "aaaa1111-bbbb" })]);
    const boom = Object.assign(new Error("revokeSession failed: 500"), { status: 500 });
    api.revokeSession.mockRejectedValue(boom);

    fireEvent.click(screen.getByRole("button", { name: "Sign out Device aaaa1111" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    });

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't sign that out");
    expect(screen.getByText("Device aaaa1111")).toBeTruthy();
  });

  it("surfaces a rejection that carries no status at all", async () => {
    await renderReady([session({ device_id: "aaaa1111-bbbb" })]);
    api.revokeSession.mockRejectedValue(undefined);

    fireEvent.click(screen.getByRole("button", { name: "Sign out Device aaaa1111" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    });

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't sign that out");
  });

  it("dismisses the confirm without revoking", async () => {
    await renderReady([session({ device_id: "aaaa1111-bbbb" })]);

    fireEvent.click(screen.getByRole("button", { name: "Sign out Device aaaa1111" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByText("Sign out this device?")).toBeNull();
    expect(api.revokeSession).not.toHaveBeenCalled();
  });

  it("refuses to close the confirm while the revoke is in flight", async () => {
    await renderReady([session({ device_id: "aaaa1111-bbbb" })]);
    const gate = deferred();
    api.revokeSession.mockReturnValue(gate.promise);

    fireEvent.click(screen.getByRole("button", { name: "Sign out Device aaaa1111" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    });

    // The request can't be recalled, so the dialog holds until it settles —
    // the user must not be left without the result.
    expect(screen.getByRole("button", { name: "Signing out…" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" }).disabled).toBe(true);
    // Esc and the backdrop bypass the disabled button entirely — `Modal` owns
    // both and neither knows this view is busy — so the guard is what actually
    // holds the dialog open.
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByText("Sign out this device?")).toBeTruthy();

    api.listSessions.mockResolvedValue([]);
    await act(async () => {
      gate.resolve(undefined);
    });
    await waitFor(() => expect(screen.queryByText("Sign out this device?")).toBeNull());
  });

  it("drops a revoke failure that lands after the view unmounts", async () => {
    const { unmount } = await renderReady([session({ device_id: "aaaa1111-bbbb" })]);
    const gate = deferred();
    api.revokeSession.mockReturnValue(gate.promise);

    fireEvent.click(screen.getByRole("button", { name: "Sign out Device aaaa1111" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    });
    unmount();

    await act(async () => {
      gate.reject(Object.assign(new Error("late"), { status: 500 }));
    });
  });
});

describe("Sessions — sign out everywhere", () => {
  it("warns that this device is included", async () => {
    await renderReady();

    fireEvent.click(screen.getByRole("button", { name: "Sign out everywhere" }));

    expect(screen.getByText("Sign out everywhere?")).toBeTruthy();
    expect(screen.getByText(/including this one\. You'll be returned/)).toBeTruthy();
  });

  it("ends the local session rather than returning to a dead view", async () => {
    // The server denylists this access token in the same call, so anything
    // authenticated after it would 401. `endSession` clears storage and routes
    // to the login page.
    await renderReady();
    api.revokeAllSessions.mockResolvedValue(undefined);

    fireEvent.click(screen.getByRole("button", { name: "Sign out everywhere" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    });

    expect(api.revokeAllSessions).toHaveBeenCalledTimes(1);
    expect(api.endSession).toHaveBeenCalledTimes(1);
    // No re-read: the token that would authorise it is already dead.
    expect(api.listSessions).toHaveBeenCalledTimes(1);
  });

  it("keeps the session when the revoke-all fails", async () => {
    await renderReady();
    api.revokeAllSessions.mockRejectedValue(new Error("nope"));

    fireEvent.click(screen.getByRole("button", { name: "Sign out everywhere" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    });

    // Signing the user out locally on a failed revoke would claim an effect
    // the server never applied.
    expect(api.endSession).not.toHaveBeenCalled();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't sign that out");
  });

  it("clears a previous notice when a new confirm opens", async () => {
    await renderReady([session({ device_id: "aaaa1111-bbbb" })]);
    api.revokeSession.mockResolvedValue(undefined);
    api.listSessions.mockResolvedValue([session({ device_id: "aaaa1111-bbbb" })]);

    fireEvent.click(screen.getByRole("button", { name: "Sign out Device aaaa1111" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    });
    await screen.findByText("Device aaaa1111 signed out.");

    fireEvent.click(screen.getByRole("button", { name: "Sign out everywhere" }));
    expect(screen.queryByText("Device aaaa1111 signed out.")).toBeNull();
  });
});
