import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, renderHook, screen, within } from "@testing-library/react";
import type { ItemRecipe, ItemState, ViewerView } from "../generated/api";
import { entry, failed, FakeSocket, playlists, video } from "../session/fakeSession";
import { viewing } from "../session/fakeViews";
import { registerExport } from "../viewer/exportTargets";
import { useLaneFocus } from "../viewer/laneFocus";
import { CLIP, commands, ITEMS, labels, MOT, renderTree, shown } from "./WorkspaceTree.fixture";

beforeEach(() => vi.stubGlobal("WebSocket", FakeSocket));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  FakeSocket.reset();
});

describe("rows", () => {
  it("shows the backend's rows as a tree with levels, hidden ones left out", async () => {
    await renderTree();
    expect(screen.getByRole("heading", { name: "Videos, 2" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: /^Playlists, 1/ })).toBeTruthy();
    expect(screen.getAllByRole("treeitem").map((item) => item.getAttribute("aria-level"))).toEqual([
      "1",
      "1",
      "1",
      "2",
      "3",
    ]);
    expect(labels()).toEqual(["v.mp4", "bad.mp4, unavailable: File not found", "mot", "A", "GT"]);
    expect(screen.getByRole("treeitem", { name: /^mot/ }).getAttribute("aria-expanded")).toBe(
      "true",
    );
    expect(screen.getByRole("treeitem", { name: /^A/ }).getAttribute("aria-setsize")).toBe("1");
  });

  it("lists each of several playlists as a row of its own, named by the item, which opens its first entry and cannot be hidden", async () => {
    const runs = playlists("runs", [
      ["Train", [entry("runs/Train/A", "A", ["GT"])]],
      ["Train (2)", [entry("runs/Train (2)/A", "A", ["GT"])]],
    ]);
    await renderTree([runs], []);
    const rows = screen.getAllByRole("treeitem");
    expect(rows.map((row) => row.getAttribute("aria-level"))).toEqual([
      "1",
      "2",
      "3",
      "1",
      "2",
      "3",
    ]);
    expect(labels()).toEqual(["runs / Train", "A", "GT", "runs / Train (2)", "A", "GT"]);

    fireEvent.doubleClick(rows[3]);
    await act(async () => {});
    expect(shown()).toMatchObject({
      active: 1,
      viewers: { 1: { item: "runs", entry: "runs/Train (2)/A" } },
    });
    // Only the playlist whose entry a viewer shows is open, not its sibling of the same item.
    const current = () =>
      screen.getAllByRole("treeitem").map((row) => row.getAttribute("aria-current"));
    expect(current()).toEqual([null, null, null, "true", "true", null]);
    // Another playlist of the item opens in a pane of its own, beside the first.
    fireEvent.doubleClick(rows[0], { ctrlKey: true });
    await act(async () => {});
    expect(current()).toEqual(["true", "true", null, "true", "true", null]);
    expect(shown()).toMatchObject({
      active: 2,
      viewers: {
        1: { item: "runs", entry: "runs/Train (2)/A" },
        2: { item: "runs", entry: "runs/Train/A", pinned: true },
      },
    });
    fireEvent.contextMenu(rows[3]);
    expect(screen.queryByRole("menuitem", { name: "Hide" })).toBeNull();
  });

  it("names no reason for a failed item when the wire carries none", async () => {
    await renderTree([failed("bad")]);
    expect(labels()).toEqual(["bad.mp4, unavailable"]);
  });

  it("shows hidden rows dimmed, with their lanes, only with Show hidden", async () => {
    await renderTree();
    expect(screen.getByRole("heading", { name: "Playlists, 1, 3 filtered out" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show hidden items" }));
    expect(screen.getByRole("heading", { name: "Playlists, 1" })).toBeTruthy();
    expect(labels().slice(2)).toEqual(["mot", "A", "GT", "DPM, hidden", "B, hidden", "GT, hidden"]);
  });

  it("collapses a row with the left arrow and expands it with the right arrow", async () => {
    await renderTree();
    const tree = screen.getByRole("tree", { name: "Playlists" });
    fireEvent.keyDown(tree, { key: "ArrowLeft" });
    expect(labels()).toEqual(["v.mp4", "bad.mp4, unavailable: File not found", "mot"]);
    fireEvent.keyDown(tree, { key: "ArrowRight" });
    expect(labels()).toHaveLength(5);
  });

  it("draws only the rows in view of a long list", async () => {
    // A browser measures the list when its scroller first reports a size.
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(private readonly measure: () => void) {}
        observe() {
          this.measure();
        }
        disconnect() {}
      },
    );
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(100);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      return { top: 0, height: this.dataset.span ? 20 : 0 } as DOMRect;
    });
    const many = Array.from({ length: 1000 }, (_, n) => ({
      ...CLIP,
      id: `v${n}`,
      label: `video ${n}`,
    }));
    await renderTree(many, []);
    expect(screen.getAllByRole("treeitem").length).toBeLessThan(40);
    expect(screen.getAllByRole("treeitem")[0].getAttribute("aria-setsize")).toBe("1000");
  });
});

describe("opening", () => {
  it("opens the item's viewer on the entry whose row is double-clicked", async () => {
    await renderTree();
    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /^A/ }));
    await act(async () => {});
    expect(commands()).toEqual([{ type: "openViewer", item: "mot" }]);
    expect(shown()).toEqual({
      active: 1,
      viewers: { 1: { item: "mot", pinned: false, entry: "mot/A" } },
    });
  });

  it("marks the rows a viewer shows as current, and says so in their hover text", async () => {
    await renderTree();
    const row = () => screen.getByRole("treeitem", { name: /^A/ });
    expect(row().title).toBe("A\nEntry 1 of 2");
    expect(row().getAttribute("aria-current")).toBeNull();
    fireEvent.doubleClick(row());
    await act(async () => {});
    expect(row().title).toBe("A\nEntry 1 of 2\nOpen in a viewer");
    expect(row().getAttribute("aria-current")).toBe("true");
    // The lane of the entry is shown inside it, not by a viewer of its own.
    expect(screen.getByRole("treeitem", { name: /^GT/ }).getAttribute("aria-current")).toBeNull();
  });

  it("opens an item without choosing its entry, and a lane's entry", async () => {
    await renderTree();
    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /^v\.mp4/ }));
    await act(async () => {});
    expect(shown()).toEqual({ active: 1, viewers: { 1: { item: "v", pinned: false } } });
    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /^GT/ }));
    await act(async () => {});
    expect(shown()).toMatchObject({ active: 2, viewers: { 2: { item: "mot", entry: "mot/A" } } });
  });

  it("asks the viewer a lane opens to give that lane the keyboard, and a row that is not a lane asks nothing", async () => {
    await renderTree();
    const apply = vi.fn();
    const pane = (viewer: number, entry: string) =>
      renderHook(() => useLaneFocus(viewer, entry, ["GT"], [0], true, apply));
    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /^v\.mp4/ }));
    await act(async () => {});
    pane(1, "");
    expect(apply).not.toHaveBeenCalled();
    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /^GT/ }));
    await act(async () => {});
    pane(2, "mot/A");
    expect(apply).toHaveBeenCalledExactlyOnceWith(0);
  });

  it("opens to the side with Ctrl, at most eight panes, and says so for the ninth, which stays closed", async () => {
    const ids = Array.from({ length: 9 }, (_, n) => `v${n + 1}`);
    await renderTree(
      ids.map((id) => video(id, false)),
      [],
    );
    for (const id of ids) {
      fireEvent.doubleClick(
        screen.getAllByRole("treeitem", { name: new RegExp(`^${id}\\.mp4`) })[0],
        { ctrlKey: true },
      );
      await act(async () => {});
    }
    expect(Object.keys(shown().viewers)).toHaveLength(8);
    expect(Object.values(shown().viewers).every((viewer) => (viewer as ViewerView).pinned)).toBe(
      true,
    );
    expect(commands().filter(({ type }) => type === "openViewer")).toHaveLength(8);
    expect(screen.getByText("Cap at 8 panes")).toBeTruthy();
  });

  it("replaces the preview in front with another entry, which starts from its beginning, and leaves the one on screen alone", async () => {
    const room = await renderTree([{ ...MOT, open: true }, CLIP]);
    const clock = { frame: 7, playing: false, speedCenti: 100, atUs: 0 };
    act(() =>
      room.publish({
        sidebar: room.presenter.sidebar,
        ...viewing(["mot", { entry: "mot/A", clock }]),
      }),
    );
    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /^A/ }));
    expect(shown().viewers[1]).toEqual({ item: "mot", pinned: false, entry: "mot/A", clock });
    fireEvent.click(screen.getByRole("button", { name: "Show hidden items" }));
    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /^B/ }));
    expect(shown()).toEqual({
      active: 2,
      viewers: { 2: { item: "mot", pinned: false, entry: "mot/B" } },
    });
  });

  it("moves with the arrow keys and opens the current row with Enter, to the side with Ctrl+Enter", async () => {
    await renderTree();
    const tree = screen.getByRole("tree", { name: "Playlists" });
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    expect(tree.getAttribute("aria-activedescendant")).toBe(
      within(tree).getAllByRole("treeitem")[1].id,
    );
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    fireEvent.keyDown(tree, { key: "Enter" });
    await act(async () => {});
    expect(shown()).toMatchObject({ active: 1, viewers: { 1: { item: "mot", entry: "mot/A" } } });
    fireEvent.keyDown(tree, { key: "Enter", ctrlKey: true });
    await act(async () => {});
    expect(shown().viewers[2]).toMatchObject({ item: "mot", entry: "mot/A", pinned: true });
  });

  it("selects on a click, ignores Space, and opens on a double click after showing the row's place", async () => {
    await renderTree();
    const tree = screen.getByRole("tree", { name: "Playlists" });
    const row = screen.getByRole("treeitem", { name: /^A/ });
    fireEvent.click(row);
    fireEvent.keyDown(tree, { key: " " });
    await act(async () => {});
    expect(commands()).toEqual([]);
    expect(tree.getAttribute("aria-activedescendant")).toBe(row.id);

    // Left collapses the entry, moves to its playlist, and collapses that.
    for (let press = 0; press < 3; press++) fireEvent.keyDown(tree, { key: "ArrowLeft" });
    expect(labels()).toEqual(["v.mp4", "bad.mp4, unavailable: File not found", "mot"]);
    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /^mot/ }));
    await act(async () => {});
    expect(shown().active).toBe(1);
    expect(labels().slice(2)).toEqual(["mot", "A", "GT"]);
  });

  it("walks on from one section's last row into the next and back, and Home and End span them all", async () => {
    const room = await renderTree();
    const videos = screen.getByRole("tree", { name: "Videos" });
    const playlists = screen.getByRole("tree", { name: "Playlists" });
    const ids = (tree: HTMLElement) =>
      within(tree)
        .getAllByRole("treeitem")
        .map((row) => row.id);
    fireEvent.click(screen.getByRole("treeitem", { name: /^A/ }));
    expect(playlists.getAttribute("aria-activedescendant")).toBe(ids(playlists)[1]);
    videos.focus();
    fireEvent.keyDown(videos, { key: "ArrowDown" });
    fireEvent.keyDown(videos, { key: "ArrowDown" });
    expect(document.activeElement).toBe(playlists);
    expect(playlists.getAttribute("aria-activedescendant")).toBe(ids(playlists)[0]);
    fireEvent.keyDown(playlists, { key: "ArrowUp" });
    expect(document.activeElement).toBe(videos);
    expect(videos.getAttribute("aria-activedescendant")).toBe(ids(videos)[1]);
    fireEvent.keyDown(videos, { key: "End" });
    expect(playlists.getAttribute("aria-activedescendant")).toBe(ids(playlists)[2]);
    fireEvent.keyDown(playlists, { key: "Home" });
    expect(videos.getAttribute("aria-activedescendant")).toBe(ids(videos)[0]);

    // A control inside another tree keeps the focus it took when the workspace changes.
    const hide = screen.getByRole("button", { name: "Hide A" });
    hide.focus();
    act(() => room.setItems([...ITEMS]));
    expect(document.activeElement).toBe(hide);
  });

  it("says why an unavailable row cannot open and sends nothing; a row still resolving does nothing", async () => {
    await renderTree([
      failed("bad", "File not found"),
      video("wait", false, { status: "Pending" }),
    ]);
    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /^bad\.mp4/ }));
    await act(async () => {});
    expect(screen.getByRole("alert").textContent).toBe("bad.mp4: File not found");
    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /^wait\.mp4/ }));
    await act(async () => {});
    expect(commands()).toEqual([]);
    expect(shown().active).toBeUndefined();
  });

  it("says when the backend would not open the viewer, and leaves the preview it would have replaced", async () => {
    await renderTree();
    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /^v\.mp4/ }));
    await act(async () => {});
    const before = shown();
    expect(before.active).toBeDefined();
    FakeSocket.refuseContent = "TooManyStreams";
    fireEvent.doubleClick(screen.getByRole("treeitem", { name: /^A/ }));
    await act(async () => {});
    expect(screen.getByRole("alert").textContent).toBe("Too many videos are open.");
    // The backend may settle the entry of the first viewer meanwhile; no pane was added or replaced.
    expect(shown().active).toBe(before.active);
    expect(Object.keys(shown().viewers)).toEqual(Object.keys(before.viewers));
  });
});

describe("items", () => {
  const recipe: ItemRecipe = { type: "Video", path: "/v.mp4", label: null, overlays: [] };
  const stored = (item: ItemState): ItemState => ({ ...item, recipe });
  const unreadable: ItemState = {
    ...failed("odd"),
    kind: "unreadable",
    label: "Unreadable item",
    resolution: { status: "Failed", code: "WorkspaceInvalid", params: {}, reason: "Unknown kind" },
  };
  const menuOf = (name: RegExp) => {
    fireEvent.contextMenu(screen.getByRole("treeitem", { name }));
    const names = screen.getAllByRole("menuitem").map((entry) => entry.textContent);
    fireEvent.keyDown(document, { key: "Escape" });
    return names;
  };

  it("offers each row what its item can do: Open says why when it cannot, Edit needs a readable item, nested rows leave the item alone", async () => {
    await renderTree(
      [
        stored(CLIP),
        stored(failed("bad", "File not found")),
        stored(video("wait", false, { status: "Pending" })),
        unreadable,
        MOT,
      ],
      [],
    );
    expect(menuOf(/^v\.mp4/)).toEqual([
      "Open",
      "Open to the Side",
      "Information",
      "Export video",
      "Edit",
      "Remove",
    ]);
    expect(menuOf(/^bad\.mp4/)).toEqual([
      "Open",
      "Open to the Side",
      "Information",
      "Edit",
      "Remove",
    ]);
    expect(menuOf(/^wait\.mp4/)).toEqual(["Edit", "Remove"]);
    expect(menuOf(/^Unreadable item/)).toEqual([
      "Open",
      "Open to the Side",
      "Information",
      "Remove",
    ]);
    expect(menuOf(/^A/)).toEqual([
      "Open",
      "Open to the Side",
      "Information",
      "Export video",
      "Hide",
    ]);
  });

  it("exports a video from its row once a viewer shows it, and says to open it first until then", async () => {
    await renderTree([stored(CLIP)], []);
    const exportItem = () => {
      fireEvent.contextMenu(screen.getByRole("treeitem", { name: /^v\.mp4/ }));
      return screen.getByRole("menuitem", { name: "Export video" }) as HTMLButtonElement;
    };
    const first = exportItem();
    expect(first.disabled).toBe(true);
    expect(first.title).toBe("Open it first");
    fireEvent.keyDown(document, { key: "Escape" });

    const run = vi.fn();
    const leave = registerExport(1, "v", "v", run);
    fireEvent.click(exportItem());
    expect(run).toHaveBeenCalledTimes(1);
    leave();
  });

  it("removes the whole item at once from the menu or with Delete, and edits it with F2", async () => {
    await renderTree([stored(CLIP), MOT], []);
    fireEvent.contextMenu(screen.getByRole("treeitem", { name: /^v\.mp4/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    await act(async () => {});
    const tree = screen.getByRole("tree", { name: "Playlists" });
    tree.focus();
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    fireEvent.keyDown(tree, { key: "Delete" });
    await act(async () => {});
    // A nested row has no Remove of its own, so only the first Delete reached the item.
    expect(commands()).toEqual([{ type: "remove", item: "v" }]);
    expect(screen.queryByRole("dialog")).toBeNull();

    cleanup();
    FakeSocket.reset();
    vi.stubGlobal(
      "fetch",
      async () => new Response(JSON.stringify({ decoders: [], resolvers: [] })),
    );
    await renderTree([stored(CLIP)], []);
    fireEvent.keyDown(screen.getByRole("tree", { name: "Videos" }), { key: "F2" });
    expect(await screen.findByRole("dialog", { name: "Edit video" })).toBeTruthy();
    expect((screen.getByLabelText("Video path") as HTMLInputElement).value).toBe("/v.mp4");
  });

  it("lists what a row stands for: a video and its overlays, a playlist's entries numbered inside it, why an item is unavailable", async () => {
    const withLane: ItemState = {
      ...stored(video("v", false)),
      recipe: {
        ...recipe,
        overlays: [{ path: "/gt.txt", handler: "MOT_FILE", label: "v.mp4", options: {} }],
      },
    };
    const runs = playlists("runs", [
      ["Train", [entry("runs/Train/A", "A", ["GT"])]],
      ["Test", [entry("runs/Test/B", "B", ["GT", "DPM"])]],
    ]);
    await renderTree([withLane, runs, failed("bad", "File not found")], []);
    const info = (name: RegExp) => {
      fireEvent.contextMenu(screen.getAllByRole("treeitem", { name })[0]);
      fireEvent.click(screen.getByRole("menuitem", { name: "Information" }));
      const dialog = screen.getByRole("dialog");
      const text = within(dialog)
        .getAllByRole("row")
        .map((row) => row.textContent);
      fireEvent.keyDown(dialog, { key: "Escape" });
      fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
      return text;
    };
    expect(info(/^v\.mp4/)).toEqual(
      expect.arrayContaining([
        "typeseekable video",
        "overlays1",
        "path/v.mp4",
        "handler typeMOT_FILE",
      ]),
    );
    expect(info(/^B/)).toEqual(
      expect.arrayContaining(["runs / Test / Entry 1", "typeplaylist entry", "lanes2"]),
    );
    expect(info(/^bad\.mp4/)).toContain("UnavailableFile not found");
  });
});
