import { reasonOf } from "../client/errors";
/** How a camera's live stream is doing as the sidebar shows it: one state per stream, the most urgent per camera. */

import { useEffect, useSyncExternalStore } from "react";
import type { LiveFeedStatus } from "../generated/api";
import type { IconName } from "../kit/icons";
import { reasonText } from "./words";
import type { LiveView } from "./view";

/** The states of a stream, most urgent first. */
export const STATES = [
  "failed",
  "reconnecting",
  "stalled",
  "connecting",
  "live",
  "paused",
  "idle",
] as const;
export type StreamState = (typeof STATES)[number];

/** Which colour marks a row: the live kind's, the caution tone's, or muted text. */
export type MarkColor = "live" | "caution" | "muted";

/** The icon and colour of each state. Only a live stream is drawn in the live colour; the others differ by glyph. */
export const MARKS: Record<StreamState, { icon: IconName; color: MarkColor }> = {
  failed: { icon: "triangle-alert", color: "caution" },
  reconnecting: { icon: "rotate-cw", color: "caution" },
  stalled: { icon: "clock", color: "caution" },
  connecting: { icon: "video-dashed", color: "muted" },
  live: { icon: "video-filled", color: "live" },
  paused: { icon: "pause", color: "muted" },
  idle: { icon: "video", color: "muted" },
};

/** One stream's state with what a row says of it (empty for idle), and, for a camera's, whether any stream is live. */
export type StreamStatus = { state: StreamState; text: string; anyLive: boolean };

export const IDLE: StreamStatus = { state: "idle", text: "", anyLive: false };
/** An open stream the backend has not yet reported on. */
export const CONNECTING: StreamStatus = {
  state: "connecting",
  text: "Connecting…",
  anyLive: false,
};

const rank = (status: StreamStatus) => STATES.indexOf(status.state);

/** The state of one feed (the video or its overlay). */
function feedStatus(feed: LiveFeedStatus): StreamStatus {
  switch (feed.status) {
    case "Connecting":
      return CONNECTING;
    case "Live":
      return { state: "live", text: "Live", anyLive: true };
    case "Stalled":
      return { state: "stalled", text: `No frames for ${feed.seconds} s`, anyLive: false };
    case "Reconnecting":
      return { state: "reconnecting", text: `Reconnecting (${feed.attempt})`, anyLive: false };
    case "Failed":
      return { state: "failed", text: reasonText(feed.reason), anyLive: false };
  }
}

/** The most urgent of `statuses`, idle when there are none; `anyLive` says whether any of them is live. */
export function mostUrgent(statuses: Iterable<StreamStatus>): StreamStatus {
  const all = [...statuses];
  const urgent = all.reduce<StreamStatus>(
    (worst, status) => (rank(status) < rank(worst) ? status : worst),
    IDLE,
  );
  return { ...urgent, anyLive: all.some((status) => status.anyLive) };
}

/**
 * What a live viewer says of its stream: the session's own failure or reconnecting first (it holds the feeds at
 * Connecting meanwhile), else paused while newer frames are held, else the video's state. The overlay has its own
 * problem line in the viewer and does not declare the stream failed.
 */
export function statusOf({
  status,
  failure,
  video,
  paused,
}: Pick<LiveView, "status" | "failure" | "video" | "paused">): StreamStatus {
  if (status === "failed")
    return { state: "failed", text: failure ? reasonOf(failure) : "Failed", anyLive: false };
  if (status === "reconnecting")
    return { state: "reconnecting", text: "Reconnecting", anyLive: false };
  const shown = feedStatus(video);
  return paused ? { state: "paused", text: "Paused", anyLive: false } : shown;
}

/** What each open live viewer (by its id) reported, with its item. */
let reports: ReadonlyMap<number, { item: string; status: StreamStatus }> = new Map();
/** `reports` by item: the most urgent of the viewers showing it. */
let held: ReadonlyMap<string, StreamStatus> = new Map();
const listeners = new Set<() => void>();

function report(viewer: number, item: string, status: StreamStatus | null) {
  const next = new Map(reports);
  if (status) next.set(viewer, { item, status });
  else next.delete(viewer);
  reports = next;
  const byItem = new Map<string, StreamStatus[]>();
  for (const { item: shown, status: reported } of next.values())
    byItem.set(shown, [...(byItem.get(shown) ?? []), reported]);
  held = new Map([...byItem].map(([shown, all]) => [shown, mostUrgent(all)]));
  listeners.forEach((listener) => listener());
}

/**
 * Reports the status of an open live viewer's stream, by viewer, for as long as the viewer shows. Panes showing one
 * item report separately, and closing one leaves the others' reports.
 */
export function useReportStatus(viewer: number, item: string, view: LiveView): void {
  const { state, text, anyLive } = statusOf(view);
  useEffect(
    () => report(viewer, item, { state, text, anyLive }),
    [viewer, item, state, text, anyLive],
  );
  useEffect(() => () => report(viewer, item, null), [viewer, item]);
}

/** The statuses the open live viewers reported, by item (the most urgent of its viewers'); a stream without a viewer showing has none. */
export function useStreamStatuses(): ReadonlyMap<string, StreamStatus> {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => held,
  );
}
