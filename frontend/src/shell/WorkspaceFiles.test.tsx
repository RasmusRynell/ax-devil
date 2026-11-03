import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GrantsContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { Shell } from "./Shell";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

/** The shell of the window's own Session, once it is seated; resolves to the Session's room. */
async function renderShell() {
  render(
    <GrantsContext.Provider value={["configuration"]}>
      <FakeSettings state={DEFAULT_SETTINGS}>
        <SessionProvider>
          <Shell version="1.2.3" />
        </SessionProvider>
      </FakeSettings>
    </GrantsContext.Provider>,
  );
  await screen.findByRole("heading", { name: "ax-devil" });
  return FakeSocket.rooms.get("room-1")!;
}

const sent = () =>
  FakeSocket.latest.received.flatMap((message) =>
    message.type === "command" && message.cmd.type === "workspace" ? [message.cmd.command] : [],
  );

describe("workspace files", () => {
  it("opens a recent workspace from the start panel", async () => {
    const path = "/work/Gate review.ax-devil.workspace";
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(Response.json([{ path, usedMs: Date.now() }]))),
    );
    await renderShell();
    const row = await screen.findByRole("button", { name: /Gate review/ });
    expect(row.title).toBe(path);
    fireEvent.click(row);
    expect(sent()).toEqual([{ type: "open", path }]);
  });

  it("offers Keep and Discard for restored changes, even with nothing in the workspace", async () => {
    const room = await renderShell();
    const restore = () =>
      act(async () => {
        room.recovered = true;
        room.setItems([]);
      });
    for (const choice of ["Keep", "Discard"]) {
      await restore();
      fireEvent.click(screen.getByRole("button", { name: choice }));
      await waitFor(() => expect(screen.queryByText("Restored unsaved changes")).toBeNull());
    }
    expect(sent()).toEqual([{ type: "keep" }, { type: "discard" }]);
  });
});
