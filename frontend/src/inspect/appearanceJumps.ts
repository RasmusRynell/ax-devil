import type { Spans } from "./appearances";

/** Where an object is seen before and after a frame: the last frame of its last span before it, the first of the next. */
export type Jumps = { last: number | null; next: number | null };

/**
 * The frames to go to from `frame`, which show the object before and after it. Inside a span there are none: the
 * object is there. `spans` are ascending and do not overlap. When they are only the first ones (`truncated`), nothing
 * is known of the frames after them.
 */
export function jumpsFrom(spans: Spans, frame: number, truncated = false): Jumps {
  if (truncated && frame > (spans.at(-1)?.[1] ?? -1)) return { last: null, next: null };
  // The first span that starts after the frame; the one before it, if any, is the last to start at or before it.
  let low = 0;
  let high = spans.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (spans[middle][0] > frame) high = middle;
    else low = middle + 1;
  }
  const before = low > 0 ? spans[low - 1] : undefined;
  if (before && frame <= before[1]) return { last: null, next: null };
  return { last: before?.[1] ?? null, next: spans[low]?.[0] ?? null };
}
