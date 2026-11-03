import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GrantsContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { usedOn } from "./recents";
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
    // A recovery copy: the row is named for the saved file, and opens the copy.
    const path = "/state/unsaved/Untitled.ax-devil.workspace";
    const name = "Gate review.ax-devil.workspace";
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(Response.json([{ path, name, usedMs: Date.now() }]))),
    );
    await renderShell();
    const row = await screen.findByRole("button", { name: /Gate review/ });
    expect(row.title).toBe(path);
    fireEvent.click(row);
    expect(sent()).toEqual([{ type: "open", path }]);
  });

  it("opens a workspace by the path typed where the window has no file dialog", async () => {
    await renderShell();
    fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Open Workspace/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Path on the server" }), {
      target: { value: " /srv/Gate.ax-devil.workspace " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    expect(sent()).toEqual([{ type: "open", path: "/srv/Gate.ax-devil.workspace" }]);
  });

  it("asks before a typed Save As replaces a file, and only then sends it again", async () => {
    FakeSocket.existingFiles.add("/srv/Gate.ax-devil.workspace");
    await renderShell();
    const saveAs = async (answer: string) => {
      fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
      fireEvent.click(screen.getByRole("menuitem", { name: /Save Workspace As/ }));
      fireEvent.change(screen.getByRole("textbox", { name: "Path on the server" }), {
        target: { value: "/srv/Gate" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      fireEvent.click(await screen.findByRole("button", { name: answer }));
    };
    const path = "/srv/Gate.ax-devil.workspace";
    await saveAs("Cancel");
    expect(sent()).toEqual([{ type: "save", path }]);
    expect(screen.queryByRole("alert")).toBeNull();
    await saveAs("Replace");
    expect(sent()).toEqual([
      { type: "save", path },
      { type: "save", path },
      { type: "save", path, replace: true },
    ]);
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

describe("when a recent workspace was used", () => {
  it("counts local calendar days, not 24-hour spans", () => {
    const now = new Date(2026, 5, 15, 0, 30).getTime();
    expect(usedOn(new Date(2026, 5, 15, 0, 5).getTime(), now)).toBe("today");
    expect(usedOn(new Date(2026, 5, 14, 23, 30).getTime(), now)).toBe("yesterday");
    expect(usedOn(new Date(2026, 5, 8, 12).getTime(), now)).toBe(
      new Date(2026, 5, 8, 12).toLocaleDateString(),
    );
  });
});
