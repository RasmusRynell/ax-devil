import { MAX_DATA_WINDOW_FRAMES } from "../protocol";

/** Share of the frame buffer budget that Sample Items may take, as a fraction of the frame window it buys. */
export const DATA_SHARE = 0.1;

/**
 * The data window of a `Want`: how many frames after and before the playhead the viewer keeps Frame Maps and Sample
 * Items for. `sampleBytes` is the sum of the mean item sizes of the subscribed lanes' sources, `null` when no lane is
 * subscribed. The window holds as many frames as a tenth of the buffer budget buys, at least the playhead, at most
 * the frame window and the protocol's bound, split like the frame window.
 */
export function dataWindow(
  sampleBytes: number | null,
  budgetBytes: number,
  frameWindow: { ahead: number; behind: number },
): { data_ahead: number; data_behind: number } {
  const { ahead, behind } = frameWindow;
  if (sampleBytes === null || ahead + behind === 0) return { data_ahead: 0, data_behind: 0 };
  const cap = Math.min(ahead + behind + 1, MAX_DATA_WINDOW_FRAMES);
  const frames =
    sampleBytes === 0
      ? cap
      : Math.min(cap, Math.max(1, Math.floor((DATA_SHARE * budgetBytes) / sampleBytes)));
  const data_ahead = Math.min(ahead, Math.round(((frames - 1) * ahead) / (ahead + behind)));
  return { data_ahead, data_behind: Math.min(behind, frames - 1 - data_ahead) };
}
