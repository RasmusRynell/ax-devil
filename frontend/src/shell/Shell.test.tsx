import { EMPTY_VIEW } from "../session/views";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import type { CommandId } from "../generated/settings";
import { installFakeFullscreen } from "../fakeFullscreen";
import { scenario } from "../session/fakeScenario";
import { FakeSocket } from "../session/fakeSession";
import { viewing } from "../session/fakeViews";
import { useViewerActions } from "../viewer/actions";
import { renderShell, showShell } from "./shellHarness";

installFakeFullscreen();

const videos = scenario("two-videos");

const playback = { togglePlay: vi.fn(), step: vi.fn() };
const offered = {
  "play-pause": () => playback.togglePlay(),
  "step-forward": () => playback.step(1),
};

// A viewer offering the playback commands, so the shell's handling is tested without a socket.
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

  it("shows the Plugins view from the View menu, and the Settings button's tooltip names its key", async () => {
    await renderShell();
    expect(screen.getByRole("button", { name: "Settings" }).getAttribute("title")).toBe(
      "Settings (Ctrl+,)",
    );
    const plugins = screen.getByRole("button", { name: "Plugins" });
    expect(plugins.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(screen.getByRole("menuitem", { name: "View" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Plugins" }));
    expect(plugins.getAttribute("aria-pressed")).toBe("true");
  });

  it("opens with the keyboard and closes with Escape", async () => {
    await renderShell();
    const view = screen.getByRole("menuitem", { name: "View" });
    view.focus();
    fireEvent.keyDown(view, { key: "ArrowDown" });
    expect(document.activeElement?.textContent).toBe("FullscreenF11");
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

  it("is not offered to a page that is not the Owner", async () => {
    await renderShell({
      access: { owner: false, fullAccess: false },
      values: { "ui.quick_setup_done": false },
    });
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
    await renderShell();
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

describe("about on the desktop", () => {
  afterEach(() => void delete window.axDevilWindow);

  it("shows the desktop's own version, and for a remote server also its address and version", async () => {
    for (const remote of [false, true]) {
      window.axDevilWindow = {
        // Only a window on another machine's server can disconnect.
        ...(remote && { disconnect: async () => undefined }),
        appVersion: async () => "0.9.0",
        onMaximizedChange: () => () => undefined,
        onFullScreenChange: () => () => undefined,
      } as unknown as Window["axDevilWindow"];
      await renderShell();
      fireEvent.click(screen.getByRole("menuitem", { name: "Help" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "About" }));
      const dialog = screen.getByRole("dialog", { name: "About ax-devil" });
      await waitFor(() => expect(dialog.textContent).toContain("Version 0.9.0"));
      // The backend's version appears only as the server's, never as the app's.
      expect(dialog.textContent?.includes("Connected to")).toBe(remote);
      expect(dialog.textContent?.includes("1.2.3")).toBe(remote);
      cleanup();
    }
  });
});

describe("diagnostics", () => {
  afterEach(() => {
    delete window.axDevilWindow;
    Reflect.deleteProperty(navigator, "clipboard");
  });

  it("copies what a bug report needs and opens the logs folder, for a caller who sees this machine's files", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const openLogs = vi.fn(async () => undefined);
    window.axDevilWindow = {
      appVersion: async () => "0.9.0",
      onMaximizedChange: () => () => undefined,
      onFullScreenChange: () => () => undefined,
      openLogs,
    } as unknown as Window["axDevilWindow"];
    const file = { path: "/cfg/settings.json", version: 1, log: "/state/logs/ax-devil.log" };
    const about = () => {
      fireEvent.click(screen.getByRole("menuitem", { name: "Help" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "About" }));
    };
    await renderShell({ file });
    about();
    await screen.findByText("Version 0.9.0");
    fireEvent.click(screen.getByRole("button", { name: "Open logs folder" }));
    expect(openLogs).toHaveBeenCalledWith("/state/logs/ax-devil.log");
    fireEvent.click(screen.getByRole("button", { name: "Copy diagnostics" }));
    const copied = (writeText.mock.calls as unknown as string[][])[0][0];
    expect(copied.split("\n")).toEqual([
      "ax-devil 0.9.0",
      "Backend 1.2.3",
      navigator.userAgent,
      "Settings /cfg/settings.json (version 1)",
      "Log /state/logs/ax-devil.log",
    ]);

    // Without the files grant the backend sends no paths, and the dialog offers nothing to copy.
    cleanup();
    await renderShell();
    about();
    await screen.findByRole("dialog", { name: "About ax-devil" });
    expect(screen.queryByRole("button", { name: "Copy diagnostics" })).toBeNull();
  });
});

describe("diagnostics without a working clipboard", () => {
  afterEach(() => void Reflect.deleteProperty(navigator, "clipboard"));
  const file = { path: "/cfg/settings.json", version: 1, log: "/state/logs/ax-devil.log" };
  const about = () => {
    fireEvent.click(screen.getByRole("menuitem", { name: "Help" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "About" }));
  };

  it("offers nothing to copy where the page has no clipboard", async () => {
    await renderShell({ file });
    about();
    await screen.findByRole("dialog", { name: "About ax-devil" });
    expect(navigator.clipboard).toBeUndefined();
    expect(screen.queryByRole("button", { name: "Copy diagnostics" })).toBeNull();
  });

  it("says why when the clipboard refuses", async () => {
    const writeText = vi.fn(async () => Promise.reject(new Error("Not allowed")));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await renderShell({ file });
    about();
    fireEvent.click(await screen.findByRole("button", { name: "Copy diagnostics" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Not allowed");
  });
});

describe("sidebar", () => {
  it("hides from the View menu and from the shortcut, and from the active view's button", async () => {
    await renderShell();
    expect(screen.getByRole("complementary", { name: "Sidebar" })).toBeTruthy();
    fireEvent.click(screen.getByRole("menuitem", { name: "View" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: /Sidebar/ }));
    expect(screen.queryByRole("complementary")).toBeNull();
    fireEvent.keyDown(document, { key: "\\", ctrlKey: true });
    expect(screen.getByRole("complementary")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Workspace" }));
    expect(screen.queryByRole("complementary")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Live" }));
    expect(screen.getAllByRole("heading", { name: "Live" })[0]).toBeTruthy();
  });
});

describe("view settings", () => {
  it("shows a refused save", async () => {
    const save = () => Promise.resolve({ reason: "full", location: "" });
    await renderShell({ save });
    fireEvent.click(screen.getByRole("menuitem", { name: "View" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: /Lane Names/ }));
    const box = await screen.findByRole("dialog", { name: "Settings" });
    expect(box.textContent).toContain("preference: full");
  });
});

describe("view menu checks", () => {
  it("mark Sidebar and Fullscreen while they are on, however they were changed", async () => {
    await renderShell();
    const marks = () => {
      fireEvent.click(screen.getByRole("menuitem", { name: "View" }));
      return [/^Sidebar/, /^Fullscreen/].map((name) =>
        screen.getByRole("menuitemcheckbox", { name }).getAttribute("aria-checked"),
      );
    };
    expect(marks()).toEqual(["true", "false"]);
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: /^Sidebar/ }));
    await act(() => document.documentElement.requestFullscreen());
    expect(marks()).toEqual(["false", "true"]);
  });
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
    act(() => room.show(videos, "a-open"));
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

  it("leave Enter alone: it opens Open Workspace only while the welcome area shows, which returns with the last viewer's close", async () => {
    await openViewer();
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText("Configure")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Close this pane" }));
    expect(screen.getByText("Configure")).toBeTruthy();
  });

  it("do nothing, and leave the key alone, while no viewer is open", async () => {
    await renderShell();
    const space = new KeyboardEvent("keydown", { key: " ", cancelable: true, bubbles: true });
    document.body.dispatchEvent(space);
    expect(space.defaultPrevented).toBe(false);
  });
});

describe("a Guest", () => {
  it("shows the presenter's window and cannot add to it", async () => {
    const room = FakeSocket.host();
    room.show(videos, "a-open");
    room.publish({
      sidebar: { view: "live", open: true },
      ...viewing(videos.ids.a),
    });
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    await showShell();
    expect(screen.getAllByRole("heading", { name: "Live" })[0]).toBeTruthy();
    expect(screen.getByRole("group", { name: "a.mp4", current: true })).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Close this pane" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("cannot open a workspace with Enter, as the greyed-out button cannot", async () => {
    const room = FakeSocket.host();
    room.publish(EMPTY_VIEW);
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    await showShell();
    expect(
      document.querySelector<HTMLButtonElement>('[data-cursor-id="sidebar:open-workspace"]')
        ?.disabled,
    ).toBe(true);
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  /** A Guest of a session whose Doc has "a" and "b" open and whose presenter shows "a". */
  async function watchAB(access: "watch" | "explore") {
    const room = FakeSocket.host(access);
    room.show(videos, "both-open");
    room.publish({
      sidebar: { view: "workspace", open: true },
      ...viewing(videos.ids.a, videos.ids.b),
    });
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    await showShell();
    return room;
  }
  const selected = () =>
    screen
      .queryAllByRole("group", { current: true })
      .map((pane) => pane.getAttribute("aria-label"));

  it("that explores keeps a video in view when the owner closes the one it was on", async () => {
    const room = await watchAB("explore");
    fireEvent.pointerDown(screen.getByRole("group", { name: "b.mp4" }));
    fireEvent.pointerDown(screen.getByRole("group", { name: "a.mp4" }));
    expect(selected()).toEqual(["a.mp4"]);
    act(() => room.show(videos, "b-open"));
    expect(selected()).toEqual(["b.mp4"]);
  });

  it("that follows leaves choosing to the presenter when the one shown closes", async () => {
    const room = await watchAB("explore");
    act(() => room.show(videos, "b-open"));
    expect(selected()).toEqual([]);
    expect(FakeSocket.latest.received.filter((message) => message.type === "view")).toEqual([]);
  });
});
