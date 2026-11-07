import { timecode } from "./timecode";

/** What `Opened` says about the video that the pane header shows. */
type Described = {
  width: number;
  height: number;
  fps: number | null;
  frame_count: number;
  duration_us: number;
};

/** The video's size, frame rate and length for the pane header, such as `640×480 · 25 fps · 0:04`; empty when unknown. */
export function videoDetails({ width, height, fps, frame_count, duration_us }: Described): string {
  const parts: string[] = [];
  if (width > 0 && height > 0) parts.push(`${width}×${height}`);
  if (fps !== null && fps > 0) {
    parts.push(`${Number(fps.toFixed(2))} fps`);
    // A video that reports no length (one frame) is as long as its frames at the rate.
    const lengthUs = duration_us > 0 ? duration_us : (frame_count / fps) * 1_000_000;
    parts.push(timecode(lengthUs, false));
  }
  return parts.join(" · ");
}
