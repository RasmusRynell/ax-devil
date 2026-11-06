import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ItemRecipe, ItemState } from "../generated/api";
import { entry, failed, FakeSocket, playlists, video } from "../session/fakeSession";
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
import { WorkspaceTree } from "./WorkspaceTree";

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

    fireEvent.click(rows[3]);
    await act(async () => {});
    expect(shown()).toMatchObject({
      active: "runs",
      viewers: { runs: { entry: "runs/Train (2)/A" } },
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
  it("opens the item's viewer on the entry whose row is clicked", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("treeitem", { name: /^A/ }));
    await act(async () => {});
    expect(commands()).toEqual([{ type: "openViewer", item: "mot" }]);
    expect(shown()).toEqual({ active: "mot", viewers: { mot: { entry: "mot/A" } } });
  });

  it("opens an item without choosing its entry, and a lane's entry", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("treeitem", { name: /^v\.mp4/ }));
    await act(async () => {});
    expect(shown()).toEqual({ active: "v", viewers: { v: {} } });
    fireEvent.click(screen.getByRole("treeitem", { name: /^GT/ }));
    await act(async () => {});
    expect(shown()).toMatchObject({ active: "mot", viewers: { mot: { entry: "mot/A" } } });
  });

  it("starts another entry from its beginning, and leaves the one on screen alone", async () => {
    const room = await renderTree([{ ...MOT, open: true }, CLIP]);
    const clock = { frame: 7, playing: false, speedCenti: 100, atUs: 0 };
    act(() =>
      room.publish({
        sidebar: room.presenter.sidebar,
        active: "mot",
        viewers: { mot: { entry: "mot/A", clock } },
      }),
    );
    fireEvent.click(screen.getByRole("treeitem", { name: /^A/ }));
    expect(shown().viewers.mot).toEqual({ entry: "mot/A", clock });
    fireEvent.click(screen.getByRole("button", { name: "Show hidden items" }));
    fireEvent.click(screen.getByRole("treeitem", { name: /^B/ }));
    expect(shown().viewers.mot).toEqual({ entry: "mot/B" });
  });

  it("moves with the arrow keys and opens the current row with Enter", async () => {
    await renderTree();
    const tree = screen.getByRole("tree", { name: "Playlists" });
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    expect(tree.getAttribute("aria-activedescendant")).toBe(
      within(tree).getAllByRole("treeitem")[1].id,
    );
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    fireEvent.keyDown(tree, { key: "Enter" });
    await act(async () => {});
    expect(shown()).toMatchObject({ active: "mot", viewers: { mot: { entry: "mot/A" } } });
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
    fireEvent.click(screen.getByRole("treeitem", { name: /^bad\.mp4/ }));
    await act(async () => {});
    expect(screen.getByRole("alert").textContent).toBe("bad.mp4: File not found");
    fireEvent.click(screen.getByRole("treeitem", { name: /^wait\.mp4/ }));
    await act(async () => {});
    expect(commands()).toEqual([]);
    expect(shown().active).toBeUndefined();
  });

  it("says when the backend would not open the viewer", async () => {
    await renderTree();
    FakeSocket.refuseContent = "TooManyStreams";
    fireEvent.click(screen.getByRole("treeitem", { name: /^A/ }));
    await act(async () => {});
    expect(screen.getByRole("alert").textContent).toBe("Too many videos are open.");
    expect(shown().active).toBeUndefined();
  });
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
        <WorkspaceTree {...LISTING} />
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
    expect(shown().active).toBe("mot");

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
    expect(menuOf(/^v\.mp4/)).toEqual(["Open", "Information", "Hide", "Edit", "Remove"]);
    expect(menuOf(/^bad\.mp4/)).toEqual(["Open", "Information", "Edit", "Remove"]);
    expect(menuOf(/^wait\.mp4/)).toEqual(["Edit", "Remove"]);
    expect(menuOf(/^Unreadable item/)).toEqual(["Open", "Information", "Remove"]);
    expect(menuOf(/^A/)).toEqual(["Open", "Information", "Hide"]);
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
