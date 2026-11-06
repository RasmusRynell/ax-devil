import { describe, expect, it } from "vite-plus/test";
import type { Axis, Layout } from "../generated/api";
import {
  dropSide,
  extent,
  largest,
  LEAST_PANE,
  leaves,
  move,
  prune,
  rects,
  remove,
  resize,
  seams,
  split,
} from "./layout";

const pane = (viewer: number): Layout => ({ type: "pane", viewer });
const cut = (axis: Axis, first: Layout, second: Layout, share = 5000): Layout => ({
  type: "split",
  axis,
  share,
  first,
  second,
});

describe("dropping on a pane", () => {
  it("takes the top or bottom band first, at least 24 px or a quarter of the height", () => {
    // 100 px tall: the band is 25% = 25 px.
    expect(dropSide(10, 24, 400, 100)).toBe("top");
    expect(dropSide(390, 76, 400, 100)).toBe("bottom");
    // 40 px tall: a quarter is 10 px, so the band is the 24 px minimum.
    expect(dropSide(390, 23, 400, 40)).toBe("top");
    expect(dropSide(10, 30, 400, 40)).toBe("bottom");
  });

  it("splits what the bands leave at the middle", () => {
    expect(dropSide(199, 50, 400, 200)).toBe("left");
    expect(dropSide(200, 50, 400, 200)).toBe("right");
  });
});

describe("splitting and moving", () => {
  it("puts the new pane first for left and top, second for right and bottom, in an equal split", () => {
    expect(split(pane(1), 1, 2, "left")).toEqual(cut("row", pane(2), pane(1)));
    expect(split(pane(1), 1, 2, "bottom")).toEqual(cut("column", pane(1), pane(2)));
  });

  it("collapses the split a closed pane leaves, and the panes around it keep their shares", () => {
    const layout = cut("row", cut("column", pane(1), pane(2), 3000), pane(3), 7000);
    expect(remove(layout, 2)).toEqual(cut("row", pane(1), pane(3), 7000));
    expect(remove(pane(1), 1)).toBeUndefined();
    expect(leaves(prune(layout, (id) => id !== 1))).toEqual([2, 3]);
  });

  it("moves a pane beside another and leaves its old place to the sibling", () => {
    const layout = cut("row", pane(1), cut("column", pane(2), pane(3)), 6000);
    expect(move(layout, 1, 3, "top")).toEqual(
      cut("column", pane(2), cut("column", pane(1), pane(3))),
    );
  });

  it("changes nothing for a drop on itself or on a pane that is not there", () => {
    const layout = cut("row", pane(1), pane(2));
    expect(move(layout, 1, 1, "left")).toBe(layout);
    expect(move(layout, 1, 9, "left")).toBe(layout);
    expect(move(layout, 9, 1, "left")).toBe(layout);
  });

  it("keeps a share inside 1 to 9999 when it is resized", () => {
    const layout = cut("row", pane(1), cut("row", pane(2), pane(3)));
    expect(resize(layout, [], 20_000)).toMatchObject({ share: 9999 });
    expect(resize(layout, [1], -5)).toMatchObject({ second: { share: 1 } });
  });
});

describe("laying out", () => {
  const size = { w: 1000, h: 600 };

  it("gives panes their share of the area, side by side or stacked", () => {
    const layout = cut("row", pane(1), cut("column", pane(2), pane(3)), 4000);
    expect([...rects(layout, size)]).toEqual([
      [1, { x: 0, y: 0, w: 400, h: 600 }],
      [2, { x: 400, y: 0, w: 600, h: 300 }],
      [3, { x: 400, y: 300, w: 600, h: 300 }],
    ]);
  });

  it("never shrinks a pane below its least size, wherever the share puts the seam", () => {
    const [seam] = seams(cut("row", pane(1), pane(2), 1), size);
    expect(seam.offset).toBe(LEAST_PANE.w);
    const [, second] = [...rects(cut("row", pane(1), pane(2), 9999), size).values()];
    expect(second.w).toBe(LEAST_PANE.w);
  });

  it("counts the panes behind a nested split when it keeps a side from shrinking", () => {
    const layout = cut("row", pane(1), cut("row", pane(2), pane(3)), 9000);
    const [one, two, three] = [...rects(layout, size).values()];
    expect(three.w).toBe(LEAST_PANE.w);
    expect(two.w).toBe(LEAST_PANE.w);
    expect(one.w).toBe(size.w - 2 * LEAST_PANE.w);
  });

  it("grows past the area when the least sizes need more room, instead of squeezing panes out of reach", () => {
    const layout = cut("row", pane(1), cut("row", pane(2), pane(3)));
    const narrow = { w: 300, h: 600 };
    expect(extent(layout, narrow)).toEqual({ w: 3 * LEAST_PANE.w, h: 600 });
    const placed = [...rects(layout, narrow).values()];
    expect(placed.map(({ w }) => w)).toEqual([LEAST_PANE.w, LEAST_PANE.w, LEAST_PANE.w]);
    expect(extent(layout, { w: 1000, h: 50 })).toEqual({ w: 1000, h: LEAST_PANE.h });
  });

  it("picks the largest pane, the first among equals", () => {
    expect(largest(cut("row", pane(1), pane(2)))).toBe(1);
    expect(largest(cut("row", pane(1), cut("column", pane(2), pane(3)), 3000))).toBe(2);
    expect(largest(undefined)).toBeUndefined();
  });
});
