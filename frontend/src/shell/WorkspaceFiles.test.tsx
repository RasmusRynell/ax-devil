import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AccessContext } from "../client/access";
import { DEFAULT_SETTINGS, FakeSettings } from "../client/fakeSettings";
import { scenario } from "../session/fakeScenario";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { usedOn } from "../client/recents";
import { ChangesProvider } from "../client/resource";
import { Shell } from "./Shell";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));

/** A window on its own backend, whose file dialogs choose `open` and `save`. */
function dialogs(open: string, save = open) {
  const pickOpenWorkspace = vi.fn(() => Promise.resolve(open));
  window.axDevilWindow = {
    onMaximizedChange: () => () => undefined,
    onFullScreenChange: () => () => undefined,
    pickOpenWorkspace,
    pickSaveWorkspace: () => Promise.resolve(save),
  } as unknown as typeof window.axDevilWindow;
  return pickOpenWorkspace;
}

afterEach(() => {
  delete window.axDevilWindow;
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

/** The shell of the window's own Session, once it is seated; resolves to the Session's room. */
async function renderShell(state = DEFAULT_SETTINGS) {
  render(
    <AccessContext.Provider value={{ owner: true, fullAccess: false }}>
      <FakeSettings state={state}>
        <ChangesProvider>
          <SessionProvider>
            <Shell version="1.2.3" />
          </SessionProvider>
        </ChangesProvider>
      </FakeSettings>
    </AccessContext.Provider>,
  );
  await screen.findByRole("heading", { name: "ax-devil" });
  return FakeSocket.rooms.get("room-1")!;
}

const sent = () =>
  FakeSocket.latest.received.flatMap((message) =>
    message.type === "command" && message.cmd.type === "workspace" ? [message.cmd.command] : [],
  );

describe("workspace files", () => {
  it("opens recent workspaces and refreshes recovery entries from Changed notices", async () => {
    // A recovery copy: the row is named for the saved file, and opens the copy.
    const path = "/state/unsaved/Untitled.ax-devil.workspace";
    const name = "Gate review.ax-devil.workspace";
    let list = [{ path, name, folder: "~/reviews", usedMs: Date.now() }];
    const recents = vi.fn(() => Promise.resolve(Response.json(list)));
    vi.stubGlobal("fetch", recents);
    await renderShell();
    const row = await screen.findByRole("button", { name: /Gate review/ });
    expect(row.textContent).toContain("~/reviews");
    expect(row.title).toBe(path);
    fireEvent.click(row);
    expect(sent()).toEqual([{ type: "open", path }]);
    // A failed first recovery write publishes no copy and no Changed notice: the valid older entry stays.
    await act(async () => {});
    expect(screen.getByRole("button", { name: /Gate review/ })).toBeTruthy();

    // A saved/recovered entry replaces the list only when its owner announces the settled filesystem change.
    list = [
      { path: "/srv/Other.ax-devil.workspace", name: "Other", folder: "/srv", usedMs: Date.now() },
    ];
    await act(async () =>
      FakeSocket.latest.emit({ type: "changed", topic: "recents", revision: 2 }),
    );
    await screen.findByRole("button", { name: /Other/ });
    expect(screen.queryByRole("button", { name: /Gate review/ })).toBeNull();
    // Failed recovery/Recents updates cannot invent an unusable entry; a reread still shows the valid list.
    const reads = recents.mock.calls.length;
    await act(async () =>
      FakeSocket.latest.emit({ type: "changed", topic: "recents", revision: 3 }),
    );
    await waitFor(() => expect(recents).toHaveBeenCalledTimes(reads + 1));
    expect(screen.getByRole("button", { name: /Other/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Other/ }));
    expect(sent()).toEqual([
      { type: "open", path },
      { type: "open", path: list[0]!.path },
    ]);
  });

  it("shows no recent workspaces heading when there are none, and Enter runs Open Workspace, the default", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(Response.json([]))),
    );
    const pick = dialogs("/srv/Gate.ax-devil.workspace");
    await renderShell();
    expect(screen.queryByText("Recent workspaces")).toBeNull();
    // Another button keeps its own Enter; with nothing focused, Enter runs Open Workspace.
    fireEvent.keyDown(screen.getByRole("button", { name: "Add Video" }), { key: "Enter" });
    expect(pick).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(pick).toHaveBeenCalled();
  });

  it("lets a shortcut bound to Enter take the key from the default Open Workspace", async () => {
    const pick = dialogs("/srv/Gate.ax-devil.workspace");
    await renderShell({
      ...DEFAULT_SETTINGS,
      shortcuts: { ...DEFAULT_SETTINGS.shortcuts, settings: "Enter" },
    });
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(screen.getByRole("dialog", { name: "Settings" })).toBeTruthy();
    expect(pick).not.toHaveBeenCalled();
  });

  it("opens the workspace the file dialog chose", async () => {
    dialogs("/srv/Gate.ax-devil.workspace");
    await renderShell();
    fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Open Workspace/ }));
    await waitFor(() =>
      expect(sent()).toEqual([{ type: "open", path: "/srv/Gate.ax-devil.workspace" }]),
    );
  });

  it("saves to the file the dialog chose, which has asked about an existing one itself", async () => {
    const path = "/srv/Gate.ax-devil.workspace";
    dialogs(path);
    await renderShell();
    fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Save Workspace As/ }));
    await waitFor(() => expect(sent()).toEqual([{ type: "save", path, replace: true }]));
  });

  it("offers Keep and Discard for restored changes, even with nothing in the workspace", async () => {
    const room = await renderShell();
    const restore = () => act(async () => room.show(scenario("empty"), "recovered"));
    for (const choice of ["Keep", "Discard"]) {
      await restore();
      fireEvent.click(screen.getByRole("button", { name: choice }));
      await waitFor(() => expect(screen.queryByText("Restored unsaved changes")).toBeNull());
    }
    expect(sent()).toEqual([{ type: "keep" }, { type: "discard" }]);
  });
});

describe("when a recent workspace was used", () => {
  it("counts local calendar days: today (a clock ahead too), a weekday for a week, then the date with its year only when it is not this year's", () => {
    const now = new Date(2026, 5, 15, 0, 30).getTime();
    const at = (...date: [number, number, number, number?]) =>
      usedOn(new Date(...date).getTime(), now);
    expect(at(2026, 5, 15, 0)).toBe("today");
    expect(at(2026, 5, 15, 9)).toBe("today");
    expect(at(2026, 5, 14, 23)).not.toMatch(/\d|today/);
    expect(at(2026, 5, 9, 12)).not.toMatch(/\d/);
    expect(at(2026, 5, 8, 12)).toMatch(/8/);
    expect(at(2026, 5, 8, 12)).not.toMatch(/2026/);
    expect(at(2025, 11, 31, 12)).toMatch(/2025/);
  });
});
