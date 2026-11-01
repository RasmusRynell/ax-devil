import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MENUS } from "../commands";
import type { CommandId } from "../generated/settings";
import { useViewerActions } from "../viewer/actions";
import { DEFAULT_SETTINGS, FakeSettings } from "../fakeSettings";
import { fakeWorkspace, resolved, video } from "../workspace/fakeEvents";
import { WorkspaceProvider } from "../workspace/WorkspaceProvider";
import { Shell } from "./Shell";
import type { WindowBridge } from "./windowBridge";

const playback = { togglePlay: vi.fn(), step: vi.fn() };

// A viewer that offers the playback commands, so the shell's handling of them is tested without a socket.
vi.mock("../playback/VideoViewer", () => ({
  VideoViewer: () => {
    useViewerActions({
      "play-pause": playback.togglePlay,
      "step-forward": () => playback.step(1),
    });
    return <input type="number" aria-label="Speed" />;
  },
}));

function renderShell(shortcuts: Partial<Record<CommandId, string | null>> = {}) {
  const workspace = fakeWorkspace();
  const state = { ...DEFAULT_SETTINGS, shortcuts: { ...DEFAULT_SETTINGS.shortcuts, ...shortcuts } };
  const shell = render(
    <FakeSettings state={state}>
      <WorkspaceProvider store={workspace.store}>
        <Shell />
      </WorkspaceProvider>
    </FakeSettings>,
  );
  return { ...shell, workspace };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  delete window.axDevilWindow;
});

describe("menu bar", () => {
  it("lists the menus in order, with each shortcut beside its item", () => {
    renderShell();
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(
      MENUS.map((menu) => menu.label),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "File" }));
    expect(screen.getByRole("menuitem", { name: /Save Workspace As/ }).textContent).toContain(
      "Ctrl+Shift+S",
    );
    fireEvent.click(screen.getByRole("menuitem", { name: /Open Recent/ }));
    expect(screen.getByRole("menuitem", { name: "None" }).hasAttribute("disabled")).toBe(true);
  });

  it("opens with the keyboard and closes with Escape", () => {
    renderShell();
    const view = screen.getByRole("menuitem", { name: "View" });
    view.focus();
    fireEvent.keyDown(view, { key: "ArrowDown" });
    expect(document.activeElement?.textContent).toContain("Toggle Fullscreen");
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(view);
  });

  it("closes when focus leaves the bar, so Tab and clicks into the app do not leave it hanging open", () => {
    renderShell();
    const file = screen.getByRole("menuitem", { name: "File" });
    file.focus();
    fireEvent.click(file);
    expect(screen.getByRole("menu")).toBeTruthy();
    // Only the focused menu button is a Tab stop.
    expect(screen.getByRole("menuitem", { name: "View" }).getAttribute("tabindex")).toBe("-1");
    act(() => screen.getByRole("button", { name: "Live" }).focus());
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("toggles a checkable item", () => {
    renderShell();
    fireEvent.click(screen.getByRole("menuitem", { name: "View" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Show Lane Names" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "View" }));
    expect(
      screen
        .getByRole("menuitemcheckbox", { name: "Show Lane Names" })
        .getAttribute("aria-checked"),
    ).toBe("true");
  });
});

describe("sidebar", () => {
  it("hides from the View menu and from the shortcut, and from the active view's button", () => {
    renderShell();
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
    renderShell();
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
    renderShell();
    fireEvent.keyDown(document, { key: "n", ctrlKey: true });
    await act(async () => {});
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says so in a browser, which has no native dialog", () => {
    renderShell();
    fireEvent.keyDown(document, { key: "n", ctrlKey: true });
    expect(screen.getByRole("alert").textContent).toContain("desktop app");
  });
});

describe("viewer shortcuts", () => {
  function openViewer(shortcuts?: Parameters<typeof renderShell>[0]) {
    const { workspace } = renderShell(shortcuts);
    act(() => workspace.source().send([video("a", true, resolved("a"))]));
  }

  it("reach the open viewer, repeat only where the command says so, and leave text fields alone", () => {
    openViewer();
    fireEvent.keyDown(document.body, { key: " " });
    fireEvent.keyDown(document.body, { key: " ", repeat: true });
    fireEvent.keyDown(document.body, { key: ".", repeat: true });
    fireEvent.keyDown(document.body, { key: ".", repeat: true });
    expect(playback.togglePlay).toHaveBeenCalledTimes(1);
    expect(playback.step).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(screen.getByRole("spinbutton", { name: "Speed" }), { key: "." });
    expect(playback.step).toHaveBeenCalledTimes(2);
  });

  it("follow the user's own key, and the default key no longer runs the command", () => {
    openViewer({ "play-pause": "Ctrl+J" });
    fireEvent.keyDown(document.body, { key: " " });
    expect(playback.togglePlay).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "j", ctrlKey: true });
    expect(playback.togglePlay).toHaveBeenCalledTimes(1);
  });

  it("do nothing, and leave the key alone, while no viewer is open", () => {
    renderShell();
    const space = new KeyboardEvent("keydown", { key: " ", cancelable: true, bubbles: true });
    document.body.dispatchEvent(space);
    expect(space.defaultPrevented).toBe(false);
  });
});
