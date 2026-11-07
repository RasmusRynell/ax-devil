import { timecode } from "./timecode";

/** What `Opened` says about the video that the pane header shows. */
type Described = {
  width: number;
  height: number;
  fps: number | null;
  frame_count: number;
  duration_us: number;
};

/**
 * Python's `f"{round(x, 2):g}"` for a positive `x`: the double's exact decimal value rounded to two places, a tie to
 * the even digit, without trailing zeros. `toFixed` is exact too but sends a tie up, so a tie is found in the exact
 * digits (`toFixed(100)` shows them all) and kept down when the last kept digit is even.
 */
function roundedRate(x: number): string {
  const exact = x.toFixed(100);
  const cut = exact.indexOf(".") + 3;
  const tie = exact[cut] === "5" && /^0*$/.test(exact.slice(cut + 1));
  const kept = exact.slice(0, cut);
  return String(Number(tie && Number(kept.at(-1)) % 2 === 0 ? kept : x.toFixed(2)));
}

/** The video's size, frame rate and length for the pane header, such as `640×480 · 25 fps · 0:04`; empty when unknown. */
export function videoDetails({ width, height, fps, frame_count, duration_us }: Described): string {
  const parts: string[] = [];
  if (width > 0 && height > 0) parts.push(`${width}×${height}`);
  if (fps !== null && fps > 0) {
    parts.push(`${roundedRate(fps)} fps`);
    // A video that reports no length (one frame) is as long as its frames at the rate.
    const lengthUs = duration_us > 0 ? duration_us : (frame_count / fps) * 1_000_000;
    parts.push(timecode(lengthUs, false));
  }
  return parts.join(" · ");
}
