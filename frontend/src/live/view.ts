/** What a live session shows the UI: one immutable snapshot, replaced only when something in it changed. */

import type { LiveFeedStatus } from "../generated/api";
import type { InspectionView } from "../lanes/contract";
import type { LaneState } from "../lanes/lanes";
import type { CatalogInfoWire, VideoFrame } from "../stream/protocol";
import { INITIAL_VIEW, type PlaybackView } from "../playback/view";

/**
 * What a live viewer shows: a `PlaybackView` the lane grid, the objects panel and the inspector read, whose clock
 * fields stay at rest (`status` is the socket's: `ready` once the stream is open), and the feeds.
 */
export type LiveView = PlaybackView & {
  /** Newer frames are not shown; the connection stays up. */
  paused: boolean;
  video: LiveFeedStatus;
  /** `null` while the video has no overlay, or before the backend has said. */
  overlay: LiveFeedStatus | null;
  /** The overlay's decoder is behind: its lane is in error until the queue drains. */
  behind: boolean;
  /** The overlay is up but has sent nothing yet; frames play without it meanwhile. */
  waiting: boolean;
};

/** What the session itself knows of the stream; the lanes, catalogs, inspection and frame come with it to `viewOf`. */
export type Parts = Pick<
  LiveView,
  "status" | "failure" | "paused" | "video" | "overlay" | "behind" | "waiting"
>;

/** The stream before the backend has said anything. */
export const REST: Parts = {
  status: "connecting",
  failure: undefined,
  paused: false,
  video: { status: "Connecting" },
  overlay: null,
  behind: false,
  waiting: false,
};

/** What the lane grid and the panels read, as the session holds it. */
export type Held = {
  lanes: readonly LaneState[];
  catalogs: readonly CatalogInfoWire[];
  inspection: InspectionView;
  /** The frame on screen. */
  frame: VideoFrame | undefined;
};

const NOTHING: Held = { lanes: [], catalogs: [], inspection: { state: "none" }, frame: undefined };

/** The view of the stream now. */
export function viewOf(
  parts: Parts,
  { lanes, catalogs, inspection, frame }: Held = NOTHING,
): LiveView {
  return {
    ...INITIAL_VIEW,
    ...parts,
    lanes,
    catalogs,
    inspection,
    selection: inspection.state === "none" ? null : inspection.selection,
    width: frame?.width ?? 0,
    height: frame?.height ?? 0,
    shown: frame && {
      sequenceId: frame.sequenceId,
      timestampUs: frame.timestampUs,
      periodUs: null,
    },
  };
}

/** Whether two statuses of a feed say the same. */
export function sameFeed(a: LiveFeedStatus | null, b: LiveFeedStatus | null): boolean {
  if (a === null || b === null) return a === b;
  if (a.status === "Reconnecting")
    return b.status === "Reconnecting" && a.attempt === b.attempt && a.reason === b.reason;
  if (a.status === "Failed") return b.status === "Failed" && a.reason === b.reason;
  return a.status === b.status;
}

/** Whether two views would draw the same; the session publishes a new one only when they differ. */
export const sameLive = (a: LiveView, b: LiveView): boolean =>
  a.status === b.status &&
  a.failure === b.failure &&
  a.paused === b.paused &&
  a.behind === b.behind &&
  a.waiting === b.waiting &&
  sameFeed(a.video, b.video) &&
  sameFeed(a.overlay, b.overlay) &&
  a.shown?.sequenceId === b.shown?.sequenceId &&
  a.width === b.width &&
  a.height === b.height &&
  a.lanes === b.lanes &&
  a.catalogs === b.catalogs &&
  a.inspection === b.inspection;
