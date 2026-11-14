/** Where the lanes of a picture go: the grid they tile. Shared by the viewer and the export page. */

import type { Hidden } from "../viewer/hidden";
import type { LaneState } from "../lanes/lanes";
import type { LaneCell } from "./renderer";

/** The indices of the lanes the user has not hidden. */
export function shownLanes(lanes: readonly LaneState[], hidden: Hidden, entry: string): number[] {
  return lanes.flatMap((lane, index) => (hidden.lane(entry, lane.label) ? [] : [index]));
}

/** Whether the entry has lanes and the user hid every one: there is nothing to play. */
export function allHidden(lanes: readonly LaneState[], shown: readonly number[]): boolean {
  return lanes.length > 0 && shown.length === 0;
}

/** The lane whose cell holds the point, or null. */
export function laneAt(cells: readonly LaneCell[], x: number, y: number): number | null {
  const found = cells.find(
    ({ rect }) => x >= rect.x && x < rect.x + rect.width && y >= rect.y && y < rect.y + rect.height,
  );
  return found ? found.lane : null;
}

/** How many columns `count` lanes sit in: one row of one or two, else the smallest square that holds them. */
export function columnsFor(count: number): number {
  return count <= 2 ? count : Math.ceil(Math.sqrt(count));
}

/** The gutter between lanes in CSS pixels (`--spacing-xs`; spacing is fixed pixels), as the reference's lane grid had. */
export const LANE_GAP = 2;

/**
 * Where each of `lanes` (lane indices) goes in a grid filling `width` by `height` CSS pixels, left to right and top to
 * bottom, `gap` pixels apart. Every row has the same column widths, so a last row with fewer lanes leaves the rest of
 * its width empty. Edges are whole pixels; the gaps are exactly `gap` wide, or the cells shrink to nothing.
 */
export function layoutLanes(
  lanes: readonly number[],
  width: number,
  height: number,
  gap: number,
): LaneCell[] {
  const columns = columnsFor(lanes.length);
  if (columns === 0) return [];
  const rows = Math.ceil(lanes.length / columns);
  const pitch = { x: (width + gap) / columns, y: (height + gap) / rows };
  return lanes.map((lane, at) => {
    const row = Math.floor(at / columns);
    const column = at - row * columns;
    const x = Math.round(column * pitch.x);
    const y = Math.round(row * pitch.y);
    return {
      lane,
      rect: {
        x,
        y,
        width: Math.max(0, Math.round((column + 1) * pitch.x - gap) - x),
        height: Math.max(0, Math.round((row + 1) * pitch.y - gap) - y),
      },
    };
  });
}
