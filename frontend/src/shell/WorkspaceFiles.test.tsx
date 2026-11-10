import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GrantsContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { folderOf, usedOn } from "../recents";
import { Shell } from "./Shell";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

/** The shell of the window's own Session, once it is seated; resolves to the Session's room. */
async function renderShell(state = DEFAULT_SETTINGS) {
  render(
    <GrantsContext.Provider value={["configuration"]}>
      <FakeSettings state={state}>
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
    const recents = vi.fn(() =>
      Promise.resolve(Response.json([{ path, name, usedMs: Date.now() }])),
    );
    vi.stubGlobal("fetch", recents);
    await renderShell();
    const row = await screen.findByRole("button", { name: /Gate review/ });
    expect(row.title).toBe(path);
    expect(row.textContent).toContain("/state/unsaved");
    fireEvent.click(row);
    expect(sent()).toEqual([{ type: "open", path }]);
    // The list is asked again once the command is answered, as the opened file's entry may have changed.
    await waitFor(() => expect(recents).toHaveBeenCalledTimes(2));
  });

  it("shows no recent workspaces heading when there are none, and Enter runs Open Workspace, the default", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(Response.json([]))),
    );
    await renderShell();
    expect(screen.queryByText("Recent workspaces")).toBeNull();
    // Another button keeps its own Enter; with nothing focused, Enter runs Open Workspace.
    fireEvent.keyDown(screen.getByRole("button", { name: "Add Video" }), { key: "Enter" });
    expect(screen.queryByRole("textbox", { name: "Path on the server" })).toBeNull();
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(screen.getByRole("textbox", { name: "Path on the server" })).toBeTruthy();
  });

  it("lets a shortcut bound to Enter take the key from the default Open Workspace", async () => {
    await renderShell({
      ...DEFAULT_SETTINGS,
      shortcuts: { ...DEFAULT_SETTINGS.shortcuts, settings: "Enter" },
    });
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(screen.getByRole("dialog", { name: "Settings" })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Path on the server" })).toBeNull();
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

it("names the folder of a recent workspace, keeping a filesystem root whole", () => {
  expect(folderOf("/state/unsaved/a.ax-devil.workspace")).toBe("/state/unsaved");
  expect(folderOf("/a.ax-devil.workspace")).toBe("/");
  expect(folderOf("C:\\a.ax-devil.workspace")).toBe("C:\\");
  expect(folderOf("C:/work/a.ax-devil.workspace")).toBe("C:/work");
  expect(folderOf("C:/a.ax-devil.workspace")).toBe("C:/");
  expect(folderOf("\\\\server\\share\\a.ax-devil.workspace")).toBe("\\\\server\\share");
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
