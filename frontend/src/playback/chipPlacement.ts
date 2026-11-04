/** A rectangle in CSS pixels from the layer's top-left corner. */
export type Box = { x: number; y: number; width: number; height: number };

/** The size of an element in CSS pixels. */
export type Size = { width: number; height: number };

/** The gap between the chip and the object (`--spacing-m`; spacing is fixed pixels). */
export const GAP = 8;

/** The least room between the chip and the layer's edge (`--spacing-s`). */
export const MARGIN = 4;

/** The most average characters of an object's id and class the chip shows before eliding its middle. */
export const LABEL_CEILING = 40;

/**
 * Where the hover chip's top-left corner goes, as the reference placed it: beside the object on the side with the
 * most free room (right, left, above, below; ties in that order), the first side where the chip clears the object once
 * kept inside the layer. When no side clears it, the chip sits by the object's top-right corner and covers part of it.
 */
export function placeChip(box: Box, chip: Size, layer: Size): { left: number; top: number } {
  const clamp = (value: number, size: number, limit: number) =>
    Math.max(MARGIN, Math.min(value, limit - size - MARGIN));
  const object = {
    x: Math.round(box.x),
    y: Math.round(box.y),
    width: Math.max(1, Math.round(box.width)),
    height: Math.max(1, Math.round(box.height)),
  };
  const right = object.x + object.width;
  const bottom = object.y + object.height;
  const anchor = { x: Math.trunc(box.x + box.width), y: Math.trunc(box.y) };
  const alongY = clamp(anchor.y, chip.height, layer.height);
  const alongX = clamp(anchor.x - chip.width, chip.width, layer.width);
  const sides = [
    { room: layer.width - right - MARGIN, left: right + GAP, top: alongY },
    { room: object.x - MARGIN, left: object.x - chip.width - GAP, top: alongY },
    { room: object.y - MARGIN, left: alongX, top: object.y - chip.height - GAP },
    { room: layer.height - bottom - MARGIN, left: alongX, top: bottom + GAP },
  ].sort((a, b) => b.room - a.room);
  for (const side of sides) {
    const left = clamp(side.left, chip.width, layer.width);
    const top = clamp(side.top, chip.height, layer.height);
    const clear =
      left + chip.width <= object.x ||
      right <= left ||
      top + chip.height <= object.y ||
      bottom <= top;
    if (clear) return { left, top };
  }
  const beyond = (at: number, size: number, limit: number) => {
    const after = limit - at - MARGIN;
    const before = at - MARGIN;
    return clamp(after >= size + GAP || after >= before ? at + GAP : at - size - GAP, size, limit);
  };
  return {
    left: beyond(anchor.x, chip.width, layer.width),
    top: beyond(anchor.y, chip.height, layer.height),
  };
}

/**
 * The width the id and class may take beside the facts: what the facts leave of `room`, but never less than half of
 * it and never more than `ceiling`, the width of `LABEL_CEILING` average characters.
 */
export function labelRoom(room: number, detail: number, ceiling: number): number {
  return Math.min(Math.max(room - detail - GAP, Math.floor(room / 2)), ceiling);
}

/** `text` with its middle replaced by `…`, keeping as many characters of both ends as still `fit`. */
export function elideMiddle(text: string, fit: (text: string) => boolean): string {
  if (fit(text)) return text;
  const chars = Array.from(new Intl.Segmenter().segment(text), ({ segment }) => segment);
  const cut = (kept: number) =>
    `${chars.slice(0, Math.ceil(kept / 2)).join("")}…${chars.slice(chars.length - Math.floor(kept / 2)).join("")}`;
  let low = 0;
  let high = chars.length - 1;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (fit(cut(mid))) low = mid;
    else high = mid - 1;
  }
  return cut(low);
}
