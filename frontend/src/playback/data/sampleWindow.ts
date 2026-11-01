import type { Direction } from "../../generated/api";

/** The samples a data window needs; the TypeScript twin of `overlays::sample_window` (fixtures/sample-window). */

/** Lower is more urgent: the playhead, then the direction of travel nearest first, then the other side. */
function rank(direction: Direction, playhead: number, frame: number): number {
  const distance = Math.abs(frame - playhead);
  if (distance === 0) return 0;
  // One number for the backend's `(u8, u64)`: the tier decides, the distance orders within it.
  return (frame > playhead === (direction === "forward") ? 1 : 2) * 2 ** 40 + distance;
}

/**
 * The samples the lanes' maps select inside a data window, most urgent first, each with its ranking frame.
 *
 * `maps[l][k]` is lane `l`'s cell of frame `first + k` (a sample index; negative cells are NONE or EMPTY and select
 * nothing). A sample's ranking frame is the frame mapping to it that ranks best for `playhead` and `direction`; the
 * order is ascending (rank of that frame, sample index). Backend and frontend must agree, so both follow
 * `fixtures/sample-window/cases.json`.
 */
export function sampleWindow(
  maps: readonly Int32Array[],
  first: number,
  playhead: number,
  direction: Direction,
): Array<[sample: number, frame: number]> {
  const best = new Map<number, { rank: number; frame: number }>();
  for (const map of maps) {
    for (let k = 0; k < map.length; k++) {
      const sample = map[k];
      if (sample < 0) continue;
      const frame = first + k;
      const candidate = rank(direction, playhead, frame);
      const held = best.get(sample);
      if (held === undefined || candidate < held.rank) best.set(sample, { rank: candidate, frame });
    }
  }
  return [...best]
    .sort(([a, x], [b, y]) => x.rank - y.rank || a - b)
    .map(([sample, { frame }]) => [sample, frame]);
}
