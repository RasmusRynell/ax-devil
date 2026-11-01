// The backend's Opened message for tests: the test says how long the video is.
import type { LaneDefaultsWire, LaneInfo, ServerMessage } from "../generated/api";
import type { LaneSettings } from "./contract";
import { PLAYBACK_PROTOCOL } from "./protocol";

export const LANE_DEFAULTS: LaneDefaultsWire = {
  catalog: "built-in:standard",
  policy: { tolerance_ms: 50, sticky: { enabled: false, timeout_ms: 2050 } },
};

/** A lane's settings as the user has not touched them. */
export const LANE_SETTINGS: LaneSettings = {
  catalog: LANE_DEFAULTS.catalog,
  theme: null,
  hidden: 0,
  filter: {},
  expression: "",
  tolerance_ms: LANE_DEFAULTS.policy.tolerance_ms,
  sticky: { ...LANE_DEFAULTS.policy.sticky, opacity: 1 },
  visible: true,
};

/** `Opened` for a video of `frames` frames at 25 fps, with one plain lane unless `lanes` says otherwise. */
export const opened = (
  frames: number,
  frameBytes: number,
  lanes: LaneInfo[] = [{ label: "a.mp4", source: null }],
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
