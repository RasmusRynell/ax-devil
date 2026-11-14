import { describe, expect, it } from "vite-plus/test";
import type { Layout, View, ViewerView } from "../generated/api";
import { EMPTY_VIEW } from "../session/views";
import { leaves } from "./layout";
import {
  autoPlace,
  close,
  leftWithout,
  moved,
  open,
  openToSide,
  resized,
  reuse,
} from "./placement";

const pane = (viewer: number): Layout => ({ type: "pane", viewer });
const row = (first: Layout, second: Layout, share = 5000): Layout => ({
  type: "split",
  axis: "row",
  share,
  first,
  second,
});

/** A view of side-by-side panes, `[item, pinned]` each, numbered from 1, with `active` in front. */
function viewOf(active: number | undefined, ...panes: [item: string, pinned?: boolean][]): View {
  const viewers: Record<number, ViewerView> = {};
  let layout: Layout | undefined;
  panes.forEach(([item, pinned = false], at) => {
    viewers[at + 1] = { item, pinned };
    layout = layout ? row(layout, pane(at + 1)) : pane(at + 1);
  });
  return { ...EMPTY_VIEW, active, viewers, layout };
}

const itemsOf = (view: View) => leaves(view.layout).map((id) => view.viewers[id]?.item);

describe("ordinary Open", () => {
  it("opens one pane in an empty window", () => {
    const view = open(EMPTY_VIEW, "a");
    expect(view).toMatchObject({ active: 1, layout: pane(1), viewers: { 1: { item: "a" } } });
  });

  it("replaces the preview in front where it stands, keeping the panes around it", () => {
    const next = open(viewOf(2, ["a"], ["b"], ["c"]), "d", "d/1")!;
    expect(itemsOf(next)).toEqual(["a", "d", "c"]);
    expect(next.viewers[next.active!]).toEqual({ item: "d", pinned: false, entry: "d/1" });
    // The replaced viewer is gone, and its number is not reused.
    expect(Object.keys(next.viewers)).toEqual(["1", "3", "4"]);
  });

  it("reopens the shown item with a fresh preview, replacing a preview or splitting beside a pin", () => {
    const preview = viewOf(1, ["a"]);
    expect(open(preview, "a")).toMatchObject({
      active: 2,
      viewers: { 2: { item: "a", pinned: false } },
    });
    expect(itemsOf(open(viewOf(1, ["a", true]), "a")!)).toEqual(["a", "a"]);
  });

  it("automatically places an added item in the active preview, or beside its pin", () => {
    expect(itemsOf(autoPlace(viewOf(1, ["a"], ["b"]), "c")!)).toEqual(["c", "b"]);
    expect(itemsOf(autoPlace(viewOf(1, ["a", true], ["b"]), "c")!)).toEqual(["a", "c", "b"]);
  });

  it("opens to the right of a pinned pane in front, and never replaces it", () => {
    const next = open(viewOf(1, ["a", true], ["b"]), "c")!;
    expect(itemsOf(next)).toEqual(["a", "c", "b"]);
    expect(next.viewers[1]).toEqual({ item: "a", pinned: true });
    expect(next.active).toBe(3);
  });

  it("opens beside the largest pane when none is in front, whatever it holds", () => {
    const view = { ...viewOf(undefined, ["a"], ["b"]), layout: row(pane(1), pane(2), 2000) };
    expect(itemsOf(autoPlace(view, "c")!)).toEqual(["a", "b", "c"]);
    expect(itemsOf(open({ ...view, active: 7 }, "c")!)).toEqual(["a", "b", "c"]);
  });

  it("refuses a ninth pane, but replaces a preview in a full window", () => {
    const eight = viewOf(8, ...Array.from({ length: 8 }, (_, n): [string] => [`v${n}`]));
    expect(open(eight, "x")).toMatchObject({ viewers: { 9: { item: "x" } } });
    const pinned = viewOf(
      8,
      ...Array.from({ length: 8 }, (_, n): [string, boolean] => [`v${n}`, true]),
    );
    expect(open(pinned, "x")).toBeUndefined();
    expect(itemsOf(autoPlace(eight, "x")!)).toHaveLength(8);
    expect(autoPlace(pinned, "x")).toBeUndefined();
  });
});

describe("Open to the Side", () => {
  it("is pinned and goes right of the front pane even when that is a preview", () => {
    const next = openToSide(viewOf(1, ["a"], ["b"]), "c")!;
    expect(itemsOf(next)).toEqual(["a", "c", "b"]);
    expect(next.viewers[next.active!]).toEqual({ item: "c", pinned: true });
    expect(next.viewers[1]?.pinned).toBe(false);
  });

  it("opens one pinned pane in an empty window", () => {
    expect(openToSide(EMPTY_VIEW, "a")?.viewers[1]).toEqual({ item: "a", pinned: true });
  });
});

describe("bringing forward", () => {
  it("prefers the front pane among those showing the target, and skips other entries", () => {
    const view = viewOf(2, ["a"], ["a"], ["b"]);
    view.viewers[1] = { item: "a", pinned: false, entry: "a/1" };
    expect(reuse(view, "a")).toBe(view);
    expect(reuse({ ...view, active: 3 }, "a")?.active).toBe(1);
    expect(reuse(view, "a", "a/1")?.active).toBe(1);
    expect(reuse(view, "a", "a/2")).toBeUndefined();
  });
});

describe("closing", () => {
  it("makes the first pane in layout order the front when the front one goes", () => {
    const next = close(viewOf(2, ["a"], ["b"], ["c"]), [2]);
    expect(itemsOf(next)).toEqual(["a", "c"]);
    expect(next.active).toBe(1);
    expect(close(next, [1, 3])).toMatchObject({ active: undefined, layout: undefined });
  });

  it("names the items left without a viewer, not those that keep another", () => {
    const view = viewOf(1, ["a"], ["a"], ["b"]);
    expect(leftWithout(view, close(view, [1]))).toEqual([]);
    expect(leftWithout(view, close(view, [1, 2]))).toEqual(["a"]);
    // A replaced preview leaves its item without a viewer too, unless another pane shows it.
    expect(leftWithout(view, open(view, "c")!)).toEqual([]);
    expect(leftWithout(viewOf(1, ["b"]), open(viewOf(1, ["b"]), "c")!)).toEqual(["b"]);
  });
});

describe("resizing", () => {
  it("resizes the view as it is now, and lets the panes of closed items go", () => {
    const view = viewOf(1, ["a"], ["b"], ["c"]);
    const next = resized(view, [], 3000, (id) => id !== 2);
    expect(leaves(next.layout)).toEqual([1, 3]);
    expect(Object.keys(next.viewers)).toEqual(["1", "3"]);
    expect(next.layout).toMatchObject({ share: 3000 });
  });
});

describe("moving a pane", () => {
  it("keeps its pin, brings it to the front and lets its old place collapse", () => {
    const view = viewOf(1, ["a"], ["b", true], ["c"]);
    const next = moved(view, 2, 3, "bottom");
    expect(next.active).toBe(2);
    expect(next.viewers).toEqual(view.viewers);
    expect(leaves(next.layout)).toEqual([1, 3, 2]);
    expect(moved(view, 2, 2, "left")).toBe(view);
  });
});
