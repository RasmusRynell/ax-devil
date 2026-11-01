// The backend's Opened message for tests: the test says how long the video is.
import type { LaneInfo, LaneViewWire, ServerMessage } from "../generated/api";
import { PLAYBACK_PROTOCOL } from "./protocol";

export const LANE_DEFAULTS: LaneViewWire = {
  catalog: "built-in:standard",
  filter: {},
  tolerance_ms: 50,
  sticky: { enabled: false, timeout_ms: 2050, opacity: 1 },
  target: { width: 1280, height: 720 },
  visible: true,
};

/** `Opened` for a video of `frames` frames at 25 fps, with one plain lane unless `lanes` says otherwise. */
export const opened = (
  frames: number,
  frameBytes: number,
  lanes: LaneInfo[] = [{ label: "a.mp4", has_overlay: false }],
): ServerMessage => ({
  type: "Opened",
  protocol: PLAYBACK_PROTOCOL,
  frame_count: frames,
  duration_us: frames * 40_000,
  frame_bytes: frameBytes,
  width: 64,
  height: 48,
  warnings: [],
  lanes,
  lane_defaults: LANE_DEFAULTS,
  catalogs: [],
});
