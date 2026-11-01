import type { SampleView } from "../columns";
import type { RowSet } from "../filter/filter";
import type { LayoutView } from "../layout/sink";

/**
 * Which row is under the point (`x`, `y`) of the displayed image, in CSS pixels: the smallest box that contains it, or
 * -1. It reads the kept rows, not the marks, so objects a catalog does not draw stay inspectable. Implemented in the
 * picking slice (S5).
 */
export type Pick = (
  cols: SampleView,
  rows: RowSet,
  view: LayoutView,
  x: number,
  y: number,
) => number;
