import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FakeSocket } from "../session/fakeBackend";
import { SessionProvider } from "../session/session";
import { useViewerActions } from "../viewer/actions";
import { fakeWorkspace, resolved, video } from "../workspace/fakeEvents";
import { WorkspaceProvider } from "../workspace/WorkspaceProvider";
import { Shell } from "./Shell";
import type { WindowBridge } from "./windowBridge";

const playback = { togglePlay: vi.fn(), step: vi.fn() };
const offered = {
  "play-pause": () => playback.togglePlay(),
  "step-forward": () => playback.step(1),
};

// A viewer that offers the playback commands, so the shell's handling of them is tested without a socket.
vi.mock("../playback/VideoViewer", () => ({
  VideoViewer: () => {
    useViewerActions(offered);
    return <input type="number" aria-label="Speed" />;
  },
}));

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
  delete window.axDevilWindow;
});

/** The shell inside its session and a fake workspace, shown once the backend has put the client in one. */
async function renderShell() {
  const workspace = fakeWorkspace();
  render(
    <SessionProvider>
      <WorkspaceProvider store={workspace.store}>
        <Shell />
      </WorkspaceProvider>
    </SessionProvider>,
  );
  await screen.findByRole("heading", { name: "ax-devil" });
  return workspace;
}

describe("menu bar", () => {
  it("lists only the menus with a working command, each with its shortcut", async () => {
    await renderShell();
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "File",
      "View",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toContain(
      "SettingsCtrl+,",
    );
  });

  it("opens with the keyboard and closes with Escape", async () => {
    await renderShell();
    const view = screen.getByRole("menuitem", { name: "View" });
    view.focus();
    fireEvent.keyDown(view, { key: "ArrowDown" });
    expect(document.activeElement?.textContent).toContain("Sidebar");
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(view);
  });

  it("closes when focus leaves the bar, so Tab and clicks into the app do not leave it hanging open", async () => {
    await renderShell();
    const file = screen.getByRole("menuitem", { name: "File" });
    file.focus();
    fireEvent.click(file);
    expect(screen.getByRole("menu")).toBeTruthy();
    // Only the focused menu button is a Tab stop.
    expect(screen.getByRole("menuitem", { name: "View" }).getAttribute("tabindex")).toBe("-1");
    act(() => screen.getByRole("button", { name: "Live" }).focus());
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

describe("sidebar", () => {
  it("hides from the View menu and from the shortcut, and from the active view's button", async () => {
    await renderShell();
    expect(screen.getByRole("complementary", { name: "Sidebar" })).toBeTruthy();
    fireEvent.click(screen.getByRole("menuitem", { name: "View" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Sidebar/ }));
    expect(screen.queryByRole("complementary")).toBeNull();
    fireEvent.keyDown(document, { key: "\\", ctrlKey: true });
    expect(screen.getByRole("complementary")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Workspace" }));
    expect(screen.queryByRole("complementary")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Live" }));
    expect(screen.getByRole("heading", { name: "Live" })).toBeTruthy();
  });
});

describe("add video", () => {
  const chooses = (paths: string[]) => {
    window.axDevilWindow = {
      minimize() {},
      toggleMaximize() {},
      close() {},
      pickVideos: () => Promise.resolve(paths),
      connect: () => Promise.resolve(),
      disconnect: () => Promise.resolve(),
      onMaximizedChange: () => () => {},
    } satisfies WindowBridge;
  };

  it("posts the chosen files with a viewer wanted, and says why the backend refused them", async () => {
    chooses(["/clips/a.mp4", "/clips/b.txt"]);
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 422,
      json: async () => ({ reason: "b.txt is not a video", location: "/clips/b.txt" }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    await renderShell();
    fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Add Video/ }));
    expect((await screen.findByRole("alert")).textContent).toContain("b.txt is not a video");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/workspace/items");
    expect(JSON.parse(init.body as string)).toEqual({
      items: [
        { type: "Video", path: "/clips/a.mp4" },
        { type: "Video", path: "/clips/b.txt" },
      ],
      open: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("sends nothing when the dialog is cancelled", async () => {
    chooses([]);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await renderShell();
    fireEvent.keyDown(document, { key: "n", ctrlKey: true });
    await act(async () => {});
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says so in a browser, which has no native dialog", async () => {
    await renderShell();
    fireEvent.keyDown(document, { key: "n", ctrlKey: true });
    expect(screen.getByRole("alert").textContent).toContain("desktop app");
  });
});

describe("viewer shortcuts", () => {
  async function openViewer() {
    const workspace = await renderShell();
    act(() => workspace.source().send([video("a", true, resolved("a"))]));
  }

  it("reach the open viewer, repeat only where the command says so, and leave text fields alone", async () => {
    await openViewer();
    fireEvent.keyDown(document.body, { key: " " });
    fireEvent.keyDown(document.body, { key: " ", repeat: true });
    fireEvent.keyDown(document.body, { key: ".", repeat: true });
    fireEvent.keyDown(document.body, { key: ".", repeat: true });
    expect(playback.togglePlay).toHaveBeenCalledTimes(1);
    expect(playback.step).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(screen.getByRole("spinbutton", { name: "Speed" }), { key: "." });
    expect(playback.step).toHaveBeenCalledTimes(2);
  });

  it("do nothing, and leave the key alone, while no viewer is open", async () => {
    await renderShell();
    const space = new KeyboardEvent("keydown", { key: " ", cancelable: true, bubbles: true });
    document.body.dispatchEvent(space);
    expect(space.defaultPrevented).toBe(false);
  });
});

describe("a watcher", () => {
  it("gets the welcome shell without a workspace", async () => {
    const room = FakeSocket.host();
    const join = { kind: "join", session: room.id, secret: room.secret };
    window.sessionStorage.setItem("ax-devil.credential", JSON.stringify(join));
    // No WorkspaceProvider: a watcher's shell must not ask for one.
    render(
      <SessionProvider>
        <Shell />
      </SessionProvider>,
    );
    await screen.findByRole("heading", { name: "ax-devil" });
    for (const add of screen.getAllByRole("button", { name: /Add Video/ }))
      expect((add as HTMLButtonElement).disabled).toBe(true);
  });
});
