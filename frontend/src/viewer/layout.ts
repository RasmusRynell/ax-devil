import type { Axis, Layout } from "../generated/api";

/** Where a dragged pane lands on another: it takes that half of it. */
export type Side = "left" | "right" | "top" | "bottom";

/** A box in CSS pixels, from the top left corner of the area. */
export type Rect = { x: number; y: number; w: number; h: number };

/** The size of the area the panes fill, in CSS pixels. */
export type Size = { w: number; h: number };

/** Which child to take at each split on the way down from the root: 0 the first, 1 the second. */
export type Path = readonly (0 | 1)[];

/** A split seen from outside: where its seam is and how far the seam may move. */
export interface SeamInfo {
  path: Path;
  /** The split's axis: a row split has a vertical seam. */
  axis: Axis;
  /** The space the split divides. */
  rect: Rect;
  /** The seam's distance from the start of `rect`, along the axis. */
  offset: number;
  /** The nearest and farthest `offset` the panes' least sizes allow. */
  least: number;
  most: number;
}

/**
 * [AI] The least a pane shrinks to, in CSS pixels: wide enough for its header buttons and a transport bar, and tall
 * enough for the header and a picture, which stays readable at 200% zoom in the window minimum. The Python panes keep
 * their widgets' own minimum sizes instead of one number.
 */
export const LEAST_PANE: Size = { w: 240, h: 160 };

/** The whole of a split, in shares. */
export const WHOLE = 10_000;

/** What a new split gives the first child: an equal part. */
export const HALF = WHOLE / 2;

/** [AI] The size areas are compared at when no pane has been measured, such as choosing where an opened item goes. */
const NOMINAL: Size = { w: 1600, h: 900 };

/** The viewers in the panes, in layout order. */
export function leaves(layout: Layout | undefined): number[] {
  if (!layout) return [];
  return layout.type === "pane"
    ? [layout.viewer]
    : [...leaves(layout.first), ...leaves(layout.second)];
}

/**
 * `layout` without the panes `keep` rejects. A split that loses one child becomes the other, which keeps its own
 * shares, so what surrounds a closed pane does not move; `undefined` when no pane is left.
 */
export function prune(
  layout: Layout | undefined,
  keep: (viewer: number) => boolean,
): Layout | undefined {
  if (!layout) return undefined;
  if (layout.type === "pane") return keep(layout.viewer) ? layout : undefined;
  const first = prune(layout.first, keep);
  const second = prune(layout.second, keep);
  if (first === layout.first && second === layout.second) return layout;
  return first && second ? { ...layout, first, second } : (first ?? second);
}

/** `layout` without the pane of `viewer`. */
export const remove = (layout: Layout | undefined, viewer: number): Layout | undefined =>
  prune(layout, (id) => id !== viewer);

/** `layout` with the pane of `from` showing `to` instead. */
export function swap(layout: Layout, from: number, to: number): Layout {
  if (layout.type === "pane") return layout.viewer === from ? { type: "pane", viewer: to } : layout;
  return { ...layout, first: swap(layout.first, from, to), second: swap(layout.second, from, to) };
}

/**
 * `layout` with a pane for `viewer` taking the `side` half of the pane `target`. Left and right make a row, top and
 * bottom a column; the two start equal. Without `target` in the layout, it is unchanged.
 */
export function split(layout: Layout, target: number, viewer: number, side: Side): Layout {
  if (layout.type === "split")
    return {
      ...layout,
      first: split(layout.first, target, viewer, side),
      second: split(layout.second, target, viewer, side),
    };
  if (layout.viewer !== target) return layout;
  const added: Layout = { type: "pane", viewer };
  const first = side === "left" || side === "top";
  return {
    type: "split",
    axis: side === "left" || side === "right" ? "row" : "column",
    share: HALF,
    first: first ? added : layout,
    second: first ? layout : added,
  };
}

/**
 * `layout` with the pane of `viewer` moved onto the `side` half of the pane `target`. The pane it leaves collapses
 * into its sibling. Dropping a pane on itself, or on a pane the layout does not have, changes nothing.
 */
export function move(layout: Layout, viewer: number, target: number, side: Side): Layout {
  const rest = remove(layout, viewer);
  if (
    viewer === target ||
    !rest ||
    !leaves(layout).includes(viewer) ||
    !leaves(rest).includes(target)
  )
    return layout;
  return split(rest, target, viewer, side);
}

/** `layout` with the split at `path` giving its first child `share`, kept inside 1 to 9999. */
export function resize(layout: Layout, path: Path, share: number): Layout {
  if (layout.type === "pane") return layout;
  const [head, ...rest] = path;
  if (head === undefined)
    return { ...layout, share: Math.max(1, Math.min(WHOLE - 1, Math.round(share))) };
  return head === 0
    ? { ...layout, first: resize(layout.first, rest, share) }
    : { ...layout, second: resize(layout.second, rest, share) };
}

/** The least `layout` takes along `axis`: panes in a row add their widths, and a column is as wide as its widest. */
function leastAlong(layout: Layout, axis: Axis): number {
  if (layout.type === "pane") return axis === "row" ? LEAST_PANE.w : LEAST_PANE.h;
  const [a, b] = [leastAlong(layout.first, axis), leastAlong(layout.second, axis)];
  return layout.axis === axis ? a + b : Math.max(a, b);
}

/**
 * Where a split puts its seam inside `length`: its share, kept so neither child gets less than its least. When both
 * cannot have theirs, the space divides in proportion to them, so the panes shrink together instead of overflowing.
 */
function seamAt(layout: Layout & { type: "split" }, length: number) {
  const [a, b] = [leastAlong(layout.first, layout.axis), leastAlong(layout.second, layout.axis)];
  if (a + b >= length) {
    const fixed = Math.round((length * a) / (a + b));
    return { offset: fixed, least: fixed, most: fixed };
  }
  const wanted = Math.round((length * layout.share) / WHOLE);
  return { offset: Math.max(a, Math.min(length - b, wanted)), least: a, most: length - b };
}

function lay(
  layout: Layout,
  rect: Rect,
  path: Path,
  into: { rects: Map<number, Rect>; seams: SeamInfo[] },
): void {
  if (layout.type === "pane") {
    into.rects.set(layout.viewer, rect);
    return;
  }
  const row = layout.axis === "row";
  const { offset, least, most } = seamAt(layout, row ? rect.w : rect.h);
  into.seams.push({ path, axis: layout.axis, rect, offset, least, most });
  const [first, second] = row
    ? [
        { ...rect, w: offset },
        { ...rect, x: rect.x + offset, w: rect.w - offset },
      ]
    : [
        { ...rect, h: offset },
        { ...rect, y: rect.y + offset, h: rect.h - offset },
      ];
  lay(layout.first, first, [...path, 0], into);
  lay(layout.second, second, [...path, 1], into);
}

function laid(layout: Layout | undefined, size: Size) {
  const into = { rects: new Map<number, Rect>(), seams: [] as SeamInfo[] };
  if (layout) lay(layout, { x: 0, y: 0, ...size }, [], into);
  return into;
}

/** The box of each pane in an area of `size`, with the least sizes applied. */
export const rects = (layout: Layout | undefined, size: Size): Map<number, Rect> =>
  laid(layout, size).rects;

/** The seam of each split in an area of `size`. */
export const seams = (layout: Layout | undefined, size: Size): SeamInfo[] =>
  laid(layout, size).seams;

/** The share that puts the seam of `seam` at `offset`. */
export const shareAt = (seam: SeamInfo, offset: number): number =>
  (WHOLE * offset) / (seam.axis === "row" ? seam.rect.w : seam.rect.h);

/** The pane with the greatest area, the first in layout order among equals. */
export function largest(layout: Layout | undefined, size: Size = NOMINAL): number | undefined {
  let found: number | undefined;
  let best = -1;
  for (const [viewer, { w, h }] of rects(layout, size)) {
    if (w * h > best) [found, best] = [viewer, w * h];
  }
  return found;
}

/**
 * The half of a pane a drop at (`x`, `y`) in a pane of `w` by `h` lands on. The bands along the top and the bottom,
 * the larger of 24 pixels and a quarter of the height, come first; between them, the left half or the right.
 */
export function dropSide(x: number, y: number, w: number, h: number): Side {
  const band = Math.max(24, Math.floor(h * 0.25));
  if (y < band) return "top";
  if (y > h - band) return "bottom";
  return x < Math.floor(w / 2) ? "left" : "right";
}
