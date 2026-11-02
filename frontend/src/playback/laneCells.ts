/** Where the lanes of a picture go: the grid they tile. Shared by the viewer and the export page. */

import type { LaneCell } from "./renderer";

/** How many columns `count` lanes sit in: one row of one or two, else the smallest square that holds them. */
export function columnsFor(count: number): number {
  return count <= 2 ? count : Math.ceil(Math.sqrt(count));
}

/**
 * Where each of `lanes` (lane indices) goes in a grid filling `width` by `height` CSS pixels, left to right and top to
 * bottom. A last row with fewer lanes shares the width among them. Edges are whole pixels that neighbours share.
 */
export function layoutLanes(lanes: readonly number[], width: number, height: number): LaneCell[] {
  const columns = columnsFor(lanes.length);
  if (columns === 0) return [];
  const rows = Math.ceil(lanes.length / columns);
  return lanes.map((lane, at) => {
    const row = Math.floor(at / columns);
    const inRow = Math.min(columns, lanes.length - row * columns);
    const column = at - row * columns;
    const x = Math.round((column * width) / inRow);
    const y = Math.round((row * height) / rows);
    return {
      lane,
      rect: {
        x,
        y,
        width: Math.round(((column + 1) * width) / inRow) - x,
        height: Math.round(((row + 1) * height) / rows) - y,
      },
    };
  });
}
