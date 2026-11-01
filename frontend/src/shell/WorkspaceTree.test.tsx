import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ItemState } from "../generated/api";
import { entry, failed, FakeSocket, playlist, video, type Room } from "../session/fakeSession";
import { SessionProvider, useView } from "../session/session";
import { WorkspaceTree } from "./WorkspaceTree";

const MOT = playlist(
  "mot",
  [entry("mot/A", "A", ["GT", "DPM"]), entry("mot/B", "B", ["GT"])],
  false,
);
// A video whose entry has no lanes, so its row has no children.
const CLIP: ItemState = {
  ...video("v", false),
  resolution: {
    status: "Resolved",
    content: {
      kind: "SeekableVideo",
      id: "content-v",
      generation: 1,
      display_name: "v.mp4",
      entry: entry("content-v", "v.mp4", []),
    },
  },
};
const ITEMS = [MOT, CLIP, failed("bad", "File not found")];
const HIDDEN = [{ entry: "mot/A", lane: "DPM" }, { entry: "mot/B" }];

/** What the window shows, as the tree changed it. */
function View() {
  const [active] = useView("active");
  const [viewers] = useView("viewers");
  return <div data-testid="view">{JSON.stringify({ active, viewers })}</div>;
}

async function renderTree(items: ItemState[] = ITEMS, hidden = HIDDEN) {
  render(
    <SessionProvider>
      <WorkspaceTree />
      <View />
    </SessionProvider>,
  );
  await act(async () => {});
  const room = FakeSocket.rooms.get("room-1") as Room;
  room.hidden = hidden;
  act(() => room.setItems(items));
  return room;
}

const labels = () => screen.queryAllByRole("treeitem").map((item) => item.textContent);
const shown = () => JSON.parse(screen.getByTestId("view").textContent ?? "{}");
const commands = () =>
  FakeSocket.latest.received.flatMap((message) =>
    message.type === "command" && message.cmd.type === "content" ? [message.cmd.command] : [],
  );

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
    expect(screen.getByRole("tree", { name: "Workspace" })).toBeTruthy();
    expect(screen.getAllByRole("treeitem").map((item) => item.getAttribute("aria-level"))).toEqual([
      "1",
      "2",
      "3",
      "1",
      "1",
    ]);
    expect(labels()).toEqual(["mot", "A", "GT", "v.mp4", "bad.mp4, unavailable: File not found"]);
    const [item, entryRow] = screen.getAllByRole("treeitem");
    expect(item.getAttribute("aria-expanded")).toBe("true");
    expect(entryRow.getAttribute("aria-setsize")).toBe("1");
  });

  it("names no reason for a failed item when the wire carries none", async () => {
    await renderTree([failed("bad")]);
    expect(labels()).toEqual(["bad.mp4, unavailable"]);
  });

  it("shows hidden rows dimmed, with their lanes, only with Show hidden", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("switch", { name: "Show hidden" }));
    expect(labels().slice(0, 6)).toEqual([
      "mot",
      "A",
      "GT",
      "DPM, hidden",
      "B, hidden",
      "GT, hidden",
    ]);
  });

  it("collapses a row with the left arrow and expands it with the right arrow", async () => {
    await renderTree();
    const tree = screen.getByRole("tree");
    fireEvent.keyDown(tree, { key: "ArrowLeft" });
    expect(labels()).toEqual(["mot", "v.mp4", "bad.mp4, unavailable: File not found"]);
    fireEvent.keyDown(tree, { key: "ArrowRight" });
    expect(labels()).toHaveLength(5);
  });

  it("draws only the rows in view of a long list", async () => {
    const heights = { offsetHeight: 20, clientHeight: 100 };
    for (const [name, value] of Object.entries(heights))
      vi.spyOn(HTMLElement.prototype, name as "offsetHeight", "get").mockReturnValue(value);
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
    fireEvent.click(screen.getByRole("switch", { name: "Show hidden" }));
    fireEvent.click(screen.getByRole("treeitem", { name: /^B/ }));
    expect(shown().viewers.mot).toEqual({ entry: "mot/B" });
  });

  it("moves with the arrow keys and opens the current row with Enter", async () => {
    await renderTree();
    const tree = screen.getByRole("tree");
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    expect(tree.getAttribute("aria-activedescendant")).toBe(screen.getAllByRole("treeitem")[1].id);
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    fireEvent.keyDown(tree, { key: "Enter" });
    await act(async () => {});
    expect(shown()).toMatchObject({ active: "mot", viewers: { mot: { entry: "mot/A" } } });
  });

  it("opens an item that failed, so the viewer can say why", async () => {
    await renderTree([{ ...failed("bad"), open: true }]);
    fireEvent.click(screen.getByRole("treeitem", { name: /^bad\.mp4/ }));
    expect(shown().active).toBe("bad");
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
    fireEvent.click(screen.getByRole("switch", { name: "Show hidden" }));
    fireEvent.click(screen.getByRole("button", { name: "Unhide B" }));
    await act(async () => {});
    expect(commands()).toEqual([{ type: "setHidden", target: { entry: "mot/B" }, hidden: false }]);
  });

  it("follows the Doc: a hidden row leaves the tree once the backend says so", async () => {
    await renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Hide v.mp4" }));
    await act(async () => {});
    expect(labels()).toEqual(["mot", "A", "GT", "bad.mp4, unavailable: File not found"]);
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
        <WorkspaceTree />
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
    fireEvent.keyDown(screen.getByRole("tree"), { key: "F10", shiftKey: true });
    expect(screen.getByRole("menu")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
