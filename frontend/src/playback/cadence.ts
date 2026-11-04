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

/** The cadence of frames at `times` (microseconds, ascending); `null` when no two frames are apart. */
export function cadence(times: ArrayLike<number>): Cadence | null {
  const counts = new Map<number, number>();
  for (let index = 1; index < times.length; index++) {
    const gap = Math.round(times[index] - times[index - 1]);
    if (gap > 0) counts.set(gap, (counts.get(gap) ?? 0) + 1);
  }
  if (counts.size === 0) return null;
  // The sort is stable, so gaps equally common stay in the order they first occur.
  const ranked = [...counts].sort(([, countA], [, countB]) => countB - countA);
  return {
    variable: counts.size > 1,
    periodsUs: ranked.slice(0, MODES).map(([gap]) => gap),
  };
}
