import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { RowInfo } from "../generated/api";
import { FakeSocket } from "../session/fakeSession";
import { SessionProvider } from "../session/session";
import {
  commands,
  HIDDEN,
  ITEMS,
  labels,
  LISTING,
  renderTree,
  shown,
} from "./WorkspaceTree.fixture";
import { KeptStateProvider } from "../kit/keptState";
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
  it("asks to hide a lane and an entry, from the row's button or its menu", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Hide GT" }));
    await act(async () => {});
    fireEvent.contextMenu(screen.getByRole("treeitem", { name: /^A/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Hide" }));
    await act(async () => {});
    expect(commands()).toEqual([
      { type: "setHidden", target: { entry: "mot/A", lane: "GT" }, hidden: true },
      { type: "setHidden", target: { entry: "mot/A" }, hidden: true },
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
    expect(commands()).toEqual([{ type: "setHidden", target: { entry: "mot/B" }, hidden: false }]);
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
      { type: "setHidden", target: { entry: "mot/A", lane: "DPM" }, hidden: false },
      { type: "openViewer", item: "mot" },
    ]);
    expect(shown().viewers).toMatchObject({ 1: { item: "mot", entry: "mot/A" } });
  });

  it("says why a hidden lane that is not ready cannot open, as the menu offers Open for it", async () => {
    const room = await renderTree();
    // The fake backend lists every lane ready, so this row comes from the rows it sends.
    room.tweakRows = (rows) =>
      rows.map((row): RowInfo =>
        row.kind === "lane" && row.label === "DPM"
          ? {
              ...row,
              status: {
                status: "Unavailable",
                code: "WorkspaceInvalid",
                params: {},
                reason: "File not found",
              },
            }
          : row,
      );
    act(() => room.setItems(ITEMS));
    fireEvent.click(screen.getByRole("button", { name: "Show hidden items" }));
    const lane = () => screen.getByRole("treeitem", { name: /^DPM/ });
    fireEvent.doubleClick(lane());
    expect(screen.getByRole("alert").textContent).toBe("DPM: File not found");
    fireEvent.contextMenu(lane());
    expect(screen.getByRole("menuitem", { name: "Open" }).hasAttribute("disabled")).toBe(false);
    expect(screen.queryByRole("menuitem", { name: "Unhide and open" })).toBeNull();
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
