import { afterEach, describe, expect, it, vi } from "vite-plus/test";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ItemState, RowInfo } from "../generated/api";
import { fakeWorkspace, resolved, video } from "../workspace/fakeEvents";
import { WorkspaceProvider } from "../workspace/WorkspaceProvider";
import { WorkspaceTree } from "./WorkspaceTree";

const ready = { status: "Ready" } as const;
const row = (key: string, kind: RowInfo["kind"], label: string, entry: string | null, extra = {}) =>
  ({ key, kind, label, status: ready, hidden: false, entry, ...extra }) satisfies RowInfo;

const playlist: ItemState = {
  id: "mot",
  label: "MOT",
  open: false,
  resolution: {
    status: "Resolved",
    content: {
      kind: "Playlist",
      id: "mot",
      display_name: "MOT",
      entries: [
        { id: "mot/A", label: "A", lanes: ["GT", "DPM"] },
        { id: "mot/B", label: "B", lanes: ["GT"] },
      ],
    },
  },
};
const failed: ItemState = {
  id: "bad",
  label: "bad.mp4",
  open: false,
  resolution: { status: "Failed", reason: "File not found", location: "bad.mp4" },
};
const items = [playlist, video("v", false, resolved("v")), failed];
const rows: RowInfo[] = [
  row("mot", "item", "MOT", "mot/A"),
  row("mot/A", "entry", "A", "mot/A"),
  row("mot/A\u001fGT", "lane", "GT", "mot/A"),
  row("mot/A\u001fDPM", "lane", "DPM", "mot/A", { hidden: true }),
  row("mot/B", "entry", "B", "mot/B", { hidden: true }),
  row("mot/B\u001fGT", "lane", "GT", "mot/B"),
  row("v", "item", "v.mp4", "content-v"),
  row("bad", "item", "bad.mp4", null, {
    status: { status: "Unavailable", reason: "File not found" },
  }),
];

function renderTree(state: { items: ItemState[]; rows: RowInfo[] } = { items, rows }) {
  const workspace = fakeWorkspace();
  workspace.store.start();
  const send = (next: { items: ItemState[]; rows: RowInfo[] }) =>
    workspace.source().onmessage?.(
      new MessageEvent("message", {
        data: JSON.stringify({ type: "Workspace", name: null, ...next }),
      }),
    );
  render(
    <WorkspaceProvider store={workspace.store}>
      <WorkspaceTree />
    </WorkspaceProvider>,
  );
  act(() => send(state));
  return { ...workspace, send };
}

const labels = () => screen.queryAllByRole("treeitem").map((item) => item.textContent);
const hiddenBody = () => JSON.parse(vi.mocked(fetch).mock.calls[0][1]?.body as string);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("rows", () => {
  it("shows the backend's rows as a tree with levels, hidden ones left out", () => {
    renderTree();
    expect(screen.getByRole("tree", { name: "Workspace" })).toBeTruthy();
    expect(screen.getAllByRole("treeitem").map((item) => item.getAttribute("aria-level"))).toEqual([
      "1",
      "2",
      "3",
      "1",
      "1",
    ]);
    expect(labels()).toEqual(["MOT", "A", "GT", "v.mp4", "bad.mp4, unavailable: File not found"]);
    const [item, entry] = screen.getAllByRole("treeitem");
    expect(item.getAttribute("aria-expanded")).toBe("true");
    expect(entry.getAttribute("aria-setsize")).toBe("1");
  });

  it("shows hidden rows dimmed, with their lanes, only with Show hidden", () => {
    renderTree();
    fireEvent.click(screen.getByRole("switch", { name: "Show hidden" }));
    expect(labels().slice(0, 6)).toEqual([
      "MOT",
      "A",
      "GT",
      "DPM, hidden",
      "B, hidden",
      "GT, hidden",
    ]);
  });

  it("collapses a row with the left arrow and expands it with the right arrow", () => {
    renderTree();
    const tree = screen.getByRole("tree");
    fireEvent.keyDown(tree, { key: "ArrowLeft" });
    expect(labels()).toEqual(["MOT", "v.mp4", "bad.mp4, unavailable: File not found"]);
    fireEvent.keyDown(tree, { key: "ArrowRight" });
    expect(labels()).toHaveLength(5);
  });

  it("draws only the rows in view of a long list", () => {
    const many = Array.from({ length: 1000 }, (_, n) =>
      row(`v${n}`, "item", `video ${n}`, `v${n}`),
    );
    const heights = { offsetHeight: 20, clientHeight: 100 };
    for (const [name, value] of Object.entries(heights))
      vi.spyOn(HTMLElement.prototype, name as "offsetHeight", "get").mockReturnValue(value);
    renderTree({ items: [], rows: many });
    const drawn = screen.getAllByRole("treeitem").length;
    expect(drawn).toBeLessThan(40);
    expect(screen.getAllByRole("treeitem")[0].getAttribute("aria-setsize")).toBe("1000");
  });
});

describe("opening", () => {
  it("opens an entry's viewer and asks for that entry when its row is clicked", () => {
    const { store } = renderTree();
    fireEvent.click(screen.getByRole("treeitem", { name: /^A/ }));
    expect(store.getSnapshot()).toMatchObject({
      viewers: ["mot"],
      active: "mot",
      requested: "mot/A",
    });
  });

  it("opens an item without choosing its entry, and a lane's entry", () => {
    const { store } = renderTree();
    fireEvent.click(screen.getByRole("treeitem", { name: /^v\.mp4/ }));
    expect(store.getSnapshot()).toMatchObject({ active: "v", requested: null });
    fireEvent.click(screen.getByRole("treeitem", { name: /^GT/ }));
    expect(store.getSnapshot()).toMatchObject({ active: "mot", requested: "mot/A" });
  });

  it("moves with the arrow keys and opens the current row with Enter", () => {
    const { store } = renderTree();
    const tree = screen.getByRole("tree");
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    expect(tree.getAttribute("aria-activedescendant")).toBe(screen.getAllByRole("treeitem")[1].id);
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    fireEvent.keyDown(tree, { key: "Enter" });
    expect(store.getSnapshot()).toMatchObject({ active: "mot", requested: "mot/A" });
  });

  it("opens an item that failed, so the viewer can say why", () => {
    const { store } = renderTree();
    fireEvent.click(screen.getByRole("treeitem", { name: /^bad\.mp4/ }));
    expect(store.getSnapshot().active).toBe("bad");
  });
});

describe("hiding", () => {
  const answer = (ok: boolean) =>
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        ok
          ? new Response("{}")
          : new Response(JSON.stringify({ reason: "No such entry", location: "x" }), {
              status: 400,
            }),
      ),
    );

  it("posts an entry, a lane and a video to hide, from the row menu or its button", async () => {
    answer(true);
    renderTree();
    fireEvent.contextMenu(screen.getByRole("treeitem", { name: /^A/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Hide" }));
    await act(async () => {});
    expect(hiddenBody()).toEqual({ target: { entry: "mot/A", lane: null }, hidden: true });
    fireEvent.click(screen.getByRole("button", { name: "Hide GT" }));
    await act(async () => {});
    expect(JSON.parse(vi.mocked(fetch).mock.calls[1][1]?.body as string)).toEqual({
      target: { entry: "mot/A", lane: "GT" },
      hidden: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "Hide v.mp4" }));
    await act(async () => {});
    expect(JSON.parse(vi.mocked(fetch).mock.calls[2][1]?.body as string).target).toEqual({
      entry: "content-v",
      lane: null,
    });
  });

  it("unhides a hidden row, and offers no hiding for a playlist or a failed item", async () => {
    answer(true);
    renderTree();
    expect(screen.queryByRole("button", { name: /^Hide MOT/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Hide bad/ })).toBeNull();
    fireEvent.click(screen.getByRole("switch", { name: "Show hidden" }));
    fireEvent.click(screen.getByRole("button", { name: "Unhide B" }));
    await act(async () => {});
    expect(hiddenBody()).toEqual({ target: { entry: "mot/B", lane: null }, hidden: false });
  });

  it("says why the backend refused", async () => {
    answer(false);
    renderTree();
    fireEvent.click(screen.getByRole("button", { name: "Hide A" }));
    await act(async () => {});
    expect(within(screen.getByRole("alert")).getByText("No such entry (x)")).toBeTruthy();
  });

  it("closes the row menu with Escape", () => {
    renderTree();
    fireEvent.keyDown(screen.getByRole("tree"), { key: "F10", shiftKey: true });
    expect(screen.getByRole("menu")).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("menuitem", { name: "Open" }), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
