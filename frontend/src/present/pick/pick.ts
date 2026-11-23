import type { SampleView } from "../columns";
import type { RowSet } from "../filter/filter";
import type { LayoutView } from "../layout/sink";

/**
 * Which row is under the point (`x`, `y`) of the displayed image, in CSS pixels: the smallest box that contains it, or
 * -1. A polygon counts by its bounds, as the box column stores them. It reads the kept rows, not the marks, so objects
 * a catalog does not draw or a feature hides stay pickable, and rows the filter dropped do not.
 */
export type Pick = (
  cols: SampleView,
  rows: RowSet,
  view: LayoutView,
  x: number,
  y: number,
) => number;

/** The smallest kept box containing the point; the first of equal boxes wins. */
export const pick: Pick = (cols, rows, view, x, y) => {
  const box = cols.box;
  const nx = x / view.width;
  const ny = y / view.height;
  let best = -1;
  let smallest = Infinity;
  for (let i = 0; i < rows.count; i++) {
    const row = rows.rows[i];
    const at = row * 4;
    const left = box[at];
    const top = box[at + 1];
    const right = box[at + 2];
    const bottom = box[at + 3];
    if (nx < left || nx > right || ny < top || ny > bottom) continue;
    const area = (right - left) * (bottom - top);
    if (area < smallest) {
      smallest = area;
      best = row;
    }
  }
  return best;
};
