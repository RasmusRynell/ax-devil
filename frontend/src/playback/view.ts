/** What a playback session shows the UI: one immutable snapshot that is replaced only when something in it changed. */

import type { FrameInfo } from "./clock";
import type { LaneState } from "./lanes";
import type { CatalogInfoWire } from "./protocol";

export type PlaybackView = {
  status: "connecting" | "indexing" | "ready" | "reconnecting" | "failed";
  /** Indexing progress, 0 to 1. */
  progress: number;
  failure?: { reason: string; location?: string };
  /** How many frames the video has; 0 until it is ready. */
  frameCount: number;
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
  /** The catalogs a lane may draw with, as the backend listed them when it opened the entry. */
  catalogs: readonly CatalogInfoWire[];
  /** The video's frame size in pixels; 0 until it is described. */
  width: number;
  height: number;
  /** Problems with the video itself, such as overlay sizes that do not match it. Empty for a watcher. */
  warnings: readonly string[];
};

/** The view before the backend has said anything. */
export const INITIAL_VIEW: PlaybackView = {
  status: "connecting",
  progress: 0,
  frameCount: 0,
  playing: false,
  speed: 1,
  playhead: 0,
  lanes: [],
  catalogs: [],
  width: 0,
  height: 0,
  warnings: [],
};

const sameShown = (a: FrameInfo | undefined, b: FrameInfo | undefined): boolean =>
  a === b || (a !== undefined && b !== undefined && a.sequenceId === b.sequenceId);

/** Whether two views would draw the same; the session publishes a new one only when they differ. */
export const sameView = (a: PlaybackView, b: PlaybackView): boolean =>
  a.status === b.status &&
  a.progress === b.progress &&
  a.failure === b.failure &&
  a.frameCount === b.frameCount &&
  a.playing === b.playing &&
  a.speed === b.speed &&
  a.playhead === b.playhead &&
  sameShown(a.shown, b.shown) &&
  a.frameError?.sequenceId === b.frameError?.sequenceId &&
  a.frameError?.reason === b.frameError?.reason &&
  a.lanes === b.lanes &&
  a.catalogs === b.catalogs &&
  a.width === b.width &&
  a.height === b.height &&
  a.warnings === b.warnings;
