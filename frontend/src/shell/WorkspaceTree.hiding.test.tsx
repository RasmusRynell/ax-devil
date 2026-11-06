import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { FakeSocket, video } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import {
  CLIP,
  commands,
  HIDDEN,
  ITEMS,
  labels,
  LISTING,
  MOT,
  renderTree,
  shown,
} from "./WorkspaceTree.fixture";
import { KeptStateProvider } from "./keptState";
import { WorkspaceTree } from "./WorkspaceTree";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

describe("hiding", () => {
  it("asks to hide a lane, an entry and a video, from the row's button or its menu", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Hide GT" }));
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Hide v.mp4" }));
    await act(async () => {});
    fireEvent.contextMenu(screen.getByRole("treeitem", { name: /^A/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Hide" }));
    await act(async () => {});
    expect(commands()).toEqual([
      { type: "setHidden", target: { entry: "mot/A", lane: "GT" }, hidden: true },
      { type: "setHidden", target: { entry: "content-v" }, hidden: true },
      { type: "setHidden", target: { entry: "mot/A" }, hidden: true },
    ]);
  });

  it("unhides a hidden row, and offers no hiding for a playlist or a failed item", async () => {
    await renderTree();
    expect(screen.queryByRole("button", { name: /^Hide mot/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Hide bad/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show hidden items" }));
    fireEvent.click(screen.getByRole("button", { name: "Unhide B" }));
    await act(async () => {});
    expect(commands()).toEqual([{ type: "setHidden", target: { entry: "mot/B" }, hidden: false }]);
  });

  it("follows the Doc: a hidden row leaves the tree once the backend says so", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Hide v.mp4" }));
    await act(async () => {});
    expect(labels()).toEqual(["bad.mp4, unavailable: File not found", "mot", "A", "GT"]);
  });

  it("says why the backend refused", async () => {
    await renderTree();
    FakeSocket.refuseContent = "NotAllowed";
    fireEvent.click(screen.getByRole("button", { name: "Hide A" }));
    await act(async () => {});
    expect(
      within(screen.getByRole("alert")).getByText("Your role in this session cannot do that."),
    ).toBeTruthy();
  });

  it("is disabled for a seat that may not change the workspace", async () => {
    const room = FakeSocket.host("explore");
    room.hidden = HIDDEN;
    room.setItems(ITEMS);
    window.sessionStorage.setItem(
      "ax-devil.credential",
      JSON.stringify({ kind: "join", session: room.id, secret: room.secret }),
    );
    render(
      <SessionProvider>
        <KeptStateProvider>
          <WorkspaceTree view="workspace" {...LISTING} />
        </KeptStateProvider>
      </SessionProvider>,
    );
    await act(async () => {});
    expect((screen.getByRole("button", { name: "Hide GT" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.contextMenu(screen.getByRole("treeitem", { name: /^A/ }));
    expect(screen.getByRole("menuitem", { name: "Hide" }).hasAttribute("disabled")).toBe(true);
  });

  it("keeps the focus when hiding takes the focused tree's last row away", async () => {
    // The section keeps a row, so its tree stays, but the keyboard's row is in the next section.
    await renderTree([CLIP, video("w", false), MOT], []);
    const first = screen.getByRole("tree", { name: "Videos" });
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowDown" });
    fireEvent.keyDown(first, { key: "F10", shiftKey: true });
    fireEvent.click(screen.getByRole("menuitem", { name: "Hide" }));
    await act(async () => {});
    const next = screen.getByRole("tree", { name: "Playlists" });
    expect(document.activeElement).toBe(next);
    fireEvent.keyDown(next, { key: "Enter" });
    await act(async () => {});
    expect(shown().active).toBe(1);

    cleanup();
    FakeSocket.reset();
    await renderTree([CLIP, MOT], []);
    const videos = screen.getByRole("tree", { name: "Videos" });
    videos.focus();
    fireEvent.keyDown(videos, { key: "F10", shiftKey: true });
    fireEvent.click(screen.getByRole("menuitem", { name: "Hide" }));
    await act(async () => {});
    expect(screen.queryByRole("tree", { name: "Videos" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("tree", { name: "Playlists" }));

    cleanup();
    FakeSocket.reset();
    await renderTree([CLIP], []);
    screen.getByRole("tree", { name: "Videos" }).focus();
    fireEvent.click(screen.getByRole("button", { name: "Hide v.mp4" }));
    await act(async () => {});
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Videos" }));
  });

  it("closes the row menu with Escape", async () => {
    await renderTree();
    const tree = screen.getByRole("tree", { name: "Videos" });
    fireEvent.keyDown(tree, { key: "F10", shiftKey: true });
    expect(screen.getByRole("menu")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(tree);
  });
});
