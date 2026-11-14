/** What a playback stream shows the UI: one immutable snapshot that is replaced only when something in it changed. */

import type { ErrorWire } from "../generated/api";
import type { FrameInfo, PlaybackClock } from "../stream/clock";
import type { InspectionView, Selection } from "../lanes/contract";
import type { FrameFailures } from "../stream/failures";
import type { LaneState } from "../lanes/lanes";
import type { CadenceWire, CatalogInfoWire, ServerMessage, VideoPtsWire } from "../stream/protocol";
import { videoDetails } from "./videoDetails";

export type PlaybackView = {
  status: "connecting" | "indexing" | "ready" | "reconnecting" | "failed";
  failure?: ErrorWire;
  /** How many frames the video has; 0 until it is ready. */
  frameCount: number;
  /** The time of the last frame in microseconds; 0 until it is ready. */
  durationUs: number;
  playing: boolean;
  speed: number;
  /** The position asked for, which the seek bar shows. */
  playhead: number;
  /** The frame on screen, which the readouts report. */
  shown?: FrameInfo;
  /** Why the frame at the playhead cannot be shown, while it is the one the screen is waiting on. */
  frameError?: { sequenceId: number; reason: string };
  /** The lanes of the entry, empty until the backend has described the video. */
  lanes: readonly LaneState[];
  /** The catalogs a lane may draw with, as the backend last listed them. */
  catalogs: readonly CatalogInfoWire[];
  /** The object the user selected, or `null`. */
  selection: Selection;
  /** What the inspector shows of the selection. */
  inspection: InspectionView;
  /** The video's frame size in pixels; 0 until it is described. */
  width: number;
  height: number;
  /** The size, frame rate and length for the pane header, such as `640×480 · 25 fps · 0:04`; empty until described. */
  details: string;
  /** Problems with the video itself, such as overlay sizes that do not match it. Empty for a Guest. */
  warnings: readonly string[];
  /** How evenly the video's frames are spaced; `null` until it is ready or for a one-frame video. */
  cadence: CadenceWire | null;
  /** The frames the backend holds decoded, as sorted inclusive `[first, last]` runs, which the seek bar shades. */
  cached: readonly (readonly [number, number])[];
  /** How the video's frame times come from its timestamps; `null` for an image sequence or until it is described. */
  videoPts: VideoPtsWire | null;
};

/** The view before the backend has said anything. */
export const INITIAL_VIEW: PlaybackView = {
  status: "connecting",
  frameCount: 0,
  durationUs: 0,
  playing: false,
  speed: 1,
  playhead: 0,
  lanes: [],
  catalogs: [],
  selection: null,
  inspection: { state: "none" },
  width: 0,
  height: 0,
  details: "",
  warnings: [],
  cadence: null,
  cached: [],
  videoPts: null,
};

const sameShown = (a: FrameInfo | undefined, b: FrameInfo | undefined): boolean =>
  a === b || (a !== undefined && b !== undefined && a.sequenceId === b.sequenceId);

/** Whether two views would draw the same; the stream publishes a new one only when they differ. */
export const sameView = (a: PlaybackView, b: PlaybackView): boolean =>
  a.status === b.status &&
  a.failure === b.failure &&
  a.frameCount === b.frameCount &&
  a.durationUs === b.durationUs &&
  a.playing === b.playing &&
  a.speed === b.speed &&
  a.playhead === b.playhead &&
  sameShown(a.shown, b.shown) &&
  a.frameError?.sequenceId === b.frameError?.sequenceId &&
  a.frameError?.reason === b.frameError?.reason &&
  a.lanes === b.lanes &&
  a.catalogs === b.catalogs &&
  a.inspection === b.inspection &&
  a.width === b.width &&
  a.height === b.height &&
  a.details === b.details &&
  a.warnings === b.warnings &&
  a.cached === b.cached &&
  a.videoPts === b.videoPts;

/** What the stream knows when it publishes a view. */
export type ViewParts = {
  status: PlaybackView["status"];
  failure: PlaybackView["failure"];
  frameCount: number;
  speed: number;
  clock: PlaybackClock | undefined;
  failures: FrameFailures;
  lanes: readonly LaneState[];
  catalogs: readonly CatalogInfoWire[];
  opened: Extract<ServerMessage, { type: "Opened" }> | undefined;
  inspection: InspectionView;
  cached: PlaybackView["cached"];
};

/** The view of the stream's state now. */
export function buildView(parts: ViewParts): PlaybackView {
  const { clock, opened, inspection } = parts;
  const shown = clock?.info();
  const playhead = clock?.playhead ?? 0;
  // The failure the screen is waiting on: the latest failed frame at or before the playhead that was not passed, or
  // the playhead's own, which a seek back leaves behind the frame on screen.
  const failed = parts.failures.latestAtOrBefore(playhead);
  const waiting =
    failed && (failed.sequenceId > (shown?.sequenceId ?? -1) || failed.sequenceId === playhead);
  const frameError = waiting ? failed : undefined;
  return {
    status: parts.status,
    failure: parts.failure,
    frameCount: parts.status === "ready" ? parts.frameCount : 0,
    durationUs: clock?.durationUs ?? 0,
    playing: clock?.playing ?? false,
    speed: parts.speed,
    playhead,
    shown,
    frameError,
    lanes: parts.lanes,
    catalogs: parts.catalogs,
    selection: inspection.state === "none" ? null : inspection.selection,
    inspection,
    width: opened?.width ?? 0,
    height: opened?.height ?? 0,
    details: opened ? videoDetails(opened) : "",
    warnings: opened?.warnings ?? INITIAL_VIEW.warnings,
    cadence: opened?.cadence ?? null,
    cached: parts.cached,
    videoPts: opened?.video_pts ?? null,
  };
}
