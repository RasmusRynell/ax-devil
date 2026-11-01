import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FakeSocket, video } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import { useViewerActions } from "../viewer/actions";
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

/** The shell in the window's own Session, shown once the backend has seated it. */
async function renderShell() {
  render(
    <SessionProvider>
      <Shell />
    </SessionProvider>,
  );
  await screen.findByRole("heading", { name: "ax-devil" });
  return FakeSocket.rooms.get("room-1")!;
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
  const commands = () => FakeSocket.latest.received.filter((message) => message.type === "command");

  it("asks for the chosen files with a viewer wanted, and says why the backend refused them", async () => {
    chooses(["/clips/a.mp4", "/clips/b.txt"]);
    await renderShell();
    FakeSocket.latest.defer = true;
    fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Add Video/ }));
    await act(async () => {});
    expect(commands()).toMatchObject([
      {
        cmd: {
          type: "content",
          command: {
            type: "addItems",
            items: [
              { type: "Video", path: "/clips/a.mp4" },
              { type: "Video", path: "/clips/b.txt" },
            ],
            open: true,
          },
        },
      },
    ]);
    const seq = commands()[0]!.seq;
    await act(async () =>
      FakeSocket.latest.emit({
        type: "ack",
        seq,
        outcome: { status: "refused", code: "Invalid" },
      }),
    );
    expect(screen.getByRole("alert").textContent).toContain("cannot open");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("sends nothing when the dialog is cancelled", async () => {
    chooses([]);
    await renderShell();
    fireEvent.keyDown(document, { key: "n", ctrlKey: true });
    await act(async () => {});
    expect(commands()).toEqual([]);
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
    const room = await renderShell();
    act(() => room.setItems([video("a")]));
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
  it("shows the presenter's window and cannot add to it", async () => {
    const room = FakeSocket.host();
    room.setItems([video("a")]);
    room.publish({
      sidebar: { view: "live", open: true },
      active: "a",
      viewers: { a: {} },
    });
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    render(
      <SessionProvider>
        <Shell />
      </SessionProvider>,
    );
    await screen.findByRole("heading", { name: "ax-devil" });
    expect(screen.getByRole("heading", { name: "Live" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "a.mp4" }).getAttribute("aria-selected")).toBe("true");
    expect(
      (screen.getByRole("button", { name: "Close a.mp4" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("cannot add a video from the host's files when it may only edit", async () => {
    const room = FakeSocket.host("edit");
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    render(
      <SessionProvider>
        <Shell />
      </SessionProvider>,
    );
    await screen.findByRole("heading", { name: "ax-devil" });
    const add = screen.getAllByRole("button", { name: "Add Video" });
    expect(add.every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
  });
});
