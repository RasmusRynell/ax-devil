/** How evenly a video's frames are spaced, from the frame times the backend sends. */

/** The video's frame spacing: the most common gaps between frames, most common first. */
export type Cadence = {
  /** Whether a gap differs from the most common one by more than a millisecond, such as a dropped frame. */
  variable: boolean;
  /** Up to three gaps in microseconds, most common first. */
  periodsUs: readonly number[];
};

/** A rounding of a frame time in a millisecond time base, such as 33 and 34 ms at 30 fps, is not variable. */
const JITTER_US = 1000;

/** The cadence of frames at `times` (microseconds, ascending); `null` for a one-frame video. */
export function cadence(times: ArrayLike<number>): Cadence | null {
  const counts = new Map<number, number>();
  for (let index = 1; index < times.length; index++) {
    const gap = Math.round(times[index] - times[index - 1]);
    counts.set(gap, (counts.get(gap) ?? 0) + 1);
  }
  const ranked = [...counts].sort(
    ([gapA, countA], [gapB, countB]) => countB - countA || gapA - gapB,
  );
  if (ranked.length === 0) return null;
  const usual = ranked[0][0];
  return {
    variable: ranked.some(([gap]) => Math.abs(gap - usual) > JITTER_US),
    periodsUs: ranked.slice(0, 3).map(([gap]) => gap),
  };
}
