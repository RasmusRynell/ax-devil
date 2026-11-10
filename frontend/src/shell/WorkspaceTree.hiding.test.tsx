import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { FakeSocket } from "../session/fakeSession";
import { commands, labels, renderTree, shown, TREE } from "./WorkspaceTree.fixture";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

describe("hiding", () => {
  it("asks to hide a lane and an entry, from the row's button or its menu", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Hide GT" }));
    await act(async () => {});
    fireEvent.contextMenu(screen.getByRole("treeitem", { name: /^A/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Hide" }));
    await act(async () => {});
    expect(commands()).toEqual([
      { type: "setHidden", target: { entry: TREE.ids["mot/A"], lane: "GT" }, hidden: true },
      { type: "setHidden", target: { entry: TREE.ids["mot/A"] }, hidden: true },
    ]);
  });

  it("unhides a hidden row, and offers no hiding for a video, a playlist or a failed item", async () => {
    await renderTree();
    expect(screen.queryByRole("button", { name: /^Hide v.mp4/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Hide mot/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Hide bad/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show hidden items" }));
    fireEvent.click(screen.getByRole("button", { name: "Unhide B" }));
    await act(async () => {});
    expect(commands()).toEqual([
      { type: "setHidden", target: { entry: TREE.ids["mot/B"] }, hidden: false },
    ]);
  });

  it("opens a hidden lane only through Unhide and open", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Show hidden items" }));
    const lane = () => screen.getByRole("treeitem", { name: /^DPM/ });
    fireEvent.doubleClick(lane());
    await act(async () => {});
    expect(commands()).toEqual([]);
    expect(shown().viewers).toEqual({});

    fireEvent.contextMenu(lane());
    expect(screen.getByRole("menuitem", { name: "Open" }).hasAttribute("disabled")).toBe(true);
    expect(
      screen.getByRole("menuitem", { name: "Open to the Side" }).hasAttribute("disabled"),
    ).toBe(true);
    fireEvent.click(screen.getByRole("menuitem", { name: "Unhide and open" }));
    await act(async () => {});
    expect(commands()).toEqual([
      { type: "setHidden", target: { entry: TREE.ids["mot/A"], lane: "DPM" }, hidden: false },
      { type: "openViewer", item: TREE.ids.mot },
    ]);
    expect(shown().viewers).toMatchObject({
      1: { item: TREE.ids.mot, entry: TREE.ids["mot/A"] },
    });
  });

  it("follows the Doc: a hidden row leaves the tree once the backend says so", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Hide A" }));
    await act(async () => {});
    expect(labels()).toEqual(["v.mp4", "bad.mp4, unavailable: File not found", "mot"]);
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
    await renderTree(TREE, "mot-open", "explore");
    expect((screen.getByRole("button", { name: "Hide GT" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.contextMenu(screen.getByRole("treeitem", { name: /^A/ }));
    expect(screen.getByRole("menuitem", { name: "Hide" }).hasAttribute("disabled")).toBe(true);
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
