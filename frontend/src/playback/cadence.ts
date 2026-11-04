/** How evenly a video's frames are spaced, from the frame times the backend sends. */

/** The video's frame spacing: the most common gaps between frames, most common first. */
export type Cadence = {
  /** Whether the frames have more than one distinct gap, such as after a dropped frame. */
  variable: boolean;
  /** Up to five gaps in microseconds, most common first, of which the menu shows three. */
  periodsUs: readonly number[];
};

/** How many of the most common gaps are kept. */
const MODES = 5;

/**
 * How far two gaps may differ and still be one period. Frame times are rounded to whole microseconds, so a steady
 * 30 fps in a 1/90000 base has gaps of 33333 and 33334; the exact fix is to compute cadence from the tick deltas in
 * the media index.
 */
const ROUNDING_US = 1;

/** Gaps within `ROUNDING_US` of the first gap seen, with how often each exact value occurred. */
type Cluster = { representative: number; counts: Map<number, number>; total: number };

/** The cadence of frames at `times` (microseconds, strictly ascending); `null` when no two frames are apart. */
export function cadence(times: ArrayLike<number>): Cadence | null {
  const clusters: Cluster[] = [];
  for (let index = 1; index < times.length; index++) {
    const gap = Math.round(times[index] - times[index - 1]);
    let cluster = clusters.find((c) => Math.abs(c.representative - gap) <= ROUNDING_US);
    if (!cluster) {
      cluster = { representative: gap, counts: new Map(), total: 0 };
      clusters.push(cluster);
    }
    cluster.counts.set(gap, (cluster.counts.get(gap) ?? 0) + 1);
    cluster.total++;
  }
  if (clusters.length === 0) return null;
  // The sort is stable, so periods equally common stay in the order they first occur.
  const ranked = [...clusters].sort((a, b) => b.total - a.total);
  return {
    variable: clusters.length > 1,
    periodsUs: ranked.slice(0, MODES).map((cluster) => mostCommon(cluster.counts)),
  };
}

/** The value seen most often, the first seen among equals. */
function mostCommon(counts: Map<number, number>): number {
  return [...counts].reduce((best, entry) => (entry[1] > best[1] ? entry : best))[0];
}
