import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { GrantsContext } from "../access";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import type { Grant } from "../generated/api";
import type { CommandId } from "../generated/settings";
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

/** The shell under the given settings, grants and Session (the window's own by default), once it is seated. */
async function showShell({
  shortcuts = {},
  values = {},
  grants = ["configuration"],
}: {
  shortcuts?: Partial<Record<CommandId, string | null>>;
  values?: Partial<typeof DEFAULT_SETTINGS.values>;
  grants?: Grant[];
} = {}) {
  const state = {
    ...DEFAULT_SETTINGS,
    values: { ...DEFAULT_SETTINGS.values, ...values },
    shortcuts: { ...DEFAULT_SETTINGS.shortcuts, ...shortcuts },
  };
  render(
    <GrantsContext.Provider value={grants}>
      <FakeSettings state={state}>
        <SessionProvider>
          <Shell version="1.2.3" />
        </SessionProvider>
      </FakeSettings>
    </GrantsContext.Provider>,
  );
  await screen.findByRole("heading", { name: "ax-devil" });
}

const renderShell = async (options?: Parameters<typeof showShell>[0]) => {
  await showShell(options);
  return FakeSocket.rooms.get("room-1")!;
};

describe("menu bar", () => {
  it("lists only the menus with a working command, each with its shortcut", async () => {
    await renderShell();
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "File",
      "View",
      "Help",
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

describe("quick setup", () => {
  it("opens on the first run only, and again from the Help menu", async () => {
    await renderShell();
    expect(screen.queryByRole("dialog", { name: "Quick Setup" })).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: "Help" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Quick Setup" }));
    expect(screen.getByRole("dialog", { name: "Quick Setup" })).toBeTruthy();

    cleanup();
    await renderShell({ values: { "ui.quick_setup_done": false } });
    expect(screen.getByRole("dialog", { name: "Quick Setup" })).toBeTruthy();
  });

  it("is not offered to someone who may not configure the server", async () => {
    await renderShell({ grants: ["sessions"], values: { "ui.quick_setup_done": false } });
    expect(screen.queryByRole("dialog", { name: "Quick Setup" })).toBeNull();
    fireEvent.click(screen.getByRole("menuitem", { name: "Help" }));
    expect(screen.queryByRole("menuitem", { name: "Quick Setup" })).toBeNull();
  });

  it("keeps shortcuts from running under a dialog the shell did not open", async () => {
    await renderShell();
    const foreign = document.body.appendChild(document.createElement("dialog"));
    foreign.setAttribute("open", "");
    fireEvent.keyDown(document.body, { key: ",", ctrlKey: true });
    expect(screen.queryByRole("dialog", { name: "Settings" })).toBeNull();
    foreign.remove();
    fireEvent.keyDown(document.body, { key: ",", ctrlKey: true });
    expect(screen.getByRole("dialog", { name: "Settings" })).toBeTruthy();
  });

  it("keeps the shortcuts of other dialogs from replacing it", async () => {
    await renderShell({ values: { "ui.quick_setup_done": false } });
    fireEvent.keyDown(document.body, { key: ",", ctrlKey: true });
    expect(screen.queryByRole("dialog", { name: "Settings" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Quick Setup" })).toBeTruthy();
  });
});

describe("about", () => {
  it("shows the backend's version and the project link from the Help menu, whoever the seat is", async () => {
    await renderShell({ grants: ["sessions"] });
    fireEvent.click(screen.getByRole("menuitem", { name: "Help" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "About" }));
    const dialog = screen.getByRole("dialog", { name: "About ax-devil" });
    expect(dialog.textContent).toContain("Version 1.2.3");
    expect(screen.getByRole("link").getAttribute("href")).toBe(
      "https://github.com/rasmusrynell/ax-devil",
    );
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(screen.queryByRole("dialog")).toBeNull();
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
  // The desktop's own backend grants preferences, which is how a window knows it may browse this machine's files.
  const local: Grant[] = ["configuration", "preferences"];
  const chooses = (paths: string[]) => {
    window.axDevilWindow = {
      minimize() {},
      toggleMaximize() {},
      close() {},
      pickVideos: () => Promise.resolve(paths),
      connect: () => Promise.resolve(),
      disconnect: () => Promise.resolve(),
      pickFolder: () => Promise.resolve(null),
      pickExportFile: () => Promise.resolve(null),
      onMaximizedChange: () => () => {},
    } satisfies WindowBridge;
  };
  const commands = () => FakeSocket.latest.received.filter((message) => message.type === "command");

  it("asks for the chosen files with a viewer wanted, and says why the backend refused them", async () => {
    chooses(["/clips/a.mp4", "/clips/b.txt"]);
    await renderShell({ grants: local });
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
              { type: "Video", path: "/clips/a.mp4", overlays: [] },
              { type: "Video", path: "/clips/b.txt", overlays: [] },
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
    await renderShell({ grants: local });
    fireEvent.keyDown(document, { key: "n", ctrlKey: true });
    await act(async () => {});
    expect(commands()).toEqual([]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each([
    ["a remote server's window", true],
    ["a browser", false],
  ])(
    "asks %s for a path on the server, and says why the backend refused it",
    async (_, desktop) => {
      const pick = vi.fn(() => Promise.resolve(["/clients/own.mp4"]));
      if (desktop) {
        chooses([]);
        window.axDevilWindow!.pickVideos = pick;
      }
      await renderShell({ grants: ["files"] });
      FakeSocket.latest.defer = true;
      fireEvent.keyDown(document, { key: "n", ctrlKey: true });
      fireEvent.change(screen.getByRole("textbox", { name: "Path on the server" }), {
        target: { value: " /srv/clips/a.mp4 " },
      });
      fireEvent.click(screen.getByRole("button", { name: "Add" }));
      await act(async () => {});
      expect(pick).not.toHaveBeenCalled();
      expect(commands()).toMatchObject([
        {
          cmd: {
            type: "content",
            command: {
              type: "addItems",
              items: [{ type: "Video", path: "/srv/clips/a.mp4", overlays: [] }],
              open: true,
            },
          },
        },
      ]);
      await act(async () =>
        FakeSocket.latest.emit({
          type: "ack",
          seq: commands()[0]!.seq,
          outcome: { status: "refused", code: "Invalid" },
        }),
      );
      expect(screen.getByRole("dialog", { name: "Add video" }).textContent).toContain(
        "cannot open",
      );
      // The dialog stays for another try, which closes it once accepted.
      FakeSocket.latest.defer = false;
      fireEvent.click(screen.getByRole("button", { name: "Add" }));
      await act(async () => {});
      expect(screen.queryByRole("dialog")).toBeNull();
    },
  );
});

describe("add playlist", () => {
  it("opens from the shortcut and from the File menu", async () => {
    vi.stubGlobal(
      "fetch",
      async () => new Response(JSON.stringify({ decoders: [], resolvers: [] })),
    );
    await renderShell();
    fireEvent.keyDown(document, { key: "N", ctrlKey: true, shiftKey: true });
    expect(await screen.findByRole("dialog", { name: "Add Playlist" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Add Playlist/ }));
    expect(await screen.findByRole("dialog", { name: "Add Playlist" })).toBeTruthy();
  });
});

describe("viewer shortcuts", () => {
  async function openViewer(shortcuts?: Partial<Record<CommandId, string | null>>) {
    const room = await renderShell({ shortcuts });
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

  it("follow the user's own key, and the default key no longer runs the command", async () => {
    await openViewer({ "play-pause": "Ctrl+J" });
    fireEvent.keyDown(document.body, { key: " " });
    expect(playback.togglePlay).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "j", ctrlKey: true });
    expect(playback.togglePlay).toHaveBeenCalledTimes(1);
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
    await showShell({ grants: ["sessions"] });
    expect(screen.getByRole("heading", { name: "Live" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "a.mp4" }).getAttribute("aria-selected")).toBe("true");
    expect(
      (screen.getByRole("button", { name: "Close a.mp4" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  /** A watcher of a session whose Doc has "a" and "b" open and whose presenter shows "a". */
  async function watchAB(access: "watch" | "explore") {
    const room = FakeSocket.host(access);
    room.setItems([video("a"), video("b")]);
    room.publish({
      sidebar: { view: "workspace", open: true },
      active: "a",
      viewers: { a: {}, b: {} },
    });
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    await showShell({ grants: ["sessions"] });
    return room;
  }
  const selected = () =>
    screen.queryAllByRole("tab", { selected: true }).map((tab) => tab.textContent);

  it("that explores keeps a video in view when the owner closes the one it was on", async () => {
    const room = await watchAB("explore");
    fireEvent.click(screen.getByRole("tab", { name: "b.mp4" }));
    fireEvent.click(screen.getByRole("tab", { name: "a.mp4" }));
    expect(selected()).toEqual(["a.mp4"]);
    act(() => room.setItems([video("a", false), video("b")]));
    expect(selected()).toEqual(["b.mp4"]);
  });

  it("that follows leaves choosing to the presenter when the one shown closes", async () => {
    const room = await watchAB("explore");
    act(() => room.setItems([video("a", false), video("b")]));
    expect(selected()).toEqual([]);
    expect(FakeSocket.latest.received.filter((message) => message.type === "view")).toEqual([]);
  });

  it("cannot add a video from the host's files when it may only edit", async () => {
    const room = FakeSocket.host("edit");
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    await showShell({ grants: ["sessions"] });
    const add = screen.getAllByRole("button", { name: "Add Video" });
    expect(add.every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
  });
});
