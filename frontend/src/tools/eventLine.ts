/**
 * Where the line between the events that have happened and those still to come goes in an Events list, which is in
 * frame order: before the first row on a frame after the one on screen. A list that pages knows it from the rows it has
 * loaded only when they reach both sides of the frame; otherwise the backend is asked.
 */

import type { EventsPage } from "../playback/fileEvents";

/** How many of the rows, which are in frame order, are on frames at or before `frame`. */
export function rowsUpTo(frames: readonly number[], frame: number): number {
  let low = 0;
  let high = frames.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (frames[middle] > frame) high = middle;
    else low = middle + 1;
  }
  return low;
}

/**
 * The row the line goes before, from the page alone; `null` when the page does not reach a frame at or before `frame`
 * and one after it (or the list's ends), so rows beyond it may still be on either side.
 */
export function lineOf(
  page: Pick<EventsPage, "offset" | "rows" | "matched"> | null,
  frame: number | undefined,
): number | null {
  if (!page || frame === undefined) return null;
  const { offset, rows, matched } = page;
  if (matched === 0) return 0;
  const first = rows[0];
  const last = rows.at(-1);
  if (!first || !last) return null;
  const before = offset === 0 || first.frame <= frame;
  const after = offset + rows.length >= matched || last.frame > frame;
  return before && after
    ? offset +
        rowsUpTo(
          rows.map((row) => row.frame),
          frame,
        )
    : null;
}

/**
 * The line as the list shows it: from the page, else as the backend told it for the frame it was asked about. That
 * frame is not always the one on screen by the time the answer is in, but it is where the rows to load are: the list
 * goes there, and the next page tells the line exactly.
 */
export function lineFor(page: EventsPage | null, frame: number | undefined): number | null {
  return lineOf(page, frame) ?? (page && frame !== undefined ? page.frameRow : null);
}

/**
 * Where `event` is in the list, for the rows around it to be asked for. `row` is its row as the newest page tells it:
 * from the backend's `located` when the page was asked about it, else from the rows loaded; `null` when the list does
 * not hold it or the page says nothing. `near` is `row`, or where the panel last had the event while the page says
 * nothing.
 */
export function eventRow(
  event: number | null,
  page: EventsPage | null,
  anchor: { event: number; row: number } | null,
): { row: number | null; near: number | null } {
  if (event === null) return { row: null, near: null };
  if (page?.located?.event === event) return { row: page.located.row, near: page.located.row };
  const at = page?.rows.findIndex((row) => row.event === event) ?? -1;
  const row = page && at >= 0 ? page.offset + at : null;
  return { row, near: row ?? (anchor?.event === event ? anchor.row : null) };
}
