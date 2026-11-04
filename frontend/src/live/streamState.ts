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
 * Connecting meanwhile), else its worst feed, or paused when the feeds are live but newer frames are held.
 */
export function statusOf({
  status,
  failure,
  video,
  overlay,
  paused,
}: Pick<LiveView, "status" | "failure" | "video" | "overlay" | "paused">): StreamStatus {
  if (status === "failed")
    return { state: "failed", text: failure?.reason ?? "Failed", anyLive: false };
  if (status === "reconnecting")
    return { state: "reconnecting", text: "Reconnecting", anyLive: false };
  const worst = mostUrgent([video, ...(overlay ? [overlay] : [])].map(feedStatus));
  return paused && worst.state === "live"
    ? { state: "paused", text: "Paused", anyLive: true }
    : worst;
}

let held: ReadonlyMap<string, StreamStatus> = new Map();
const listeners = new Set<() => void>();

function report(item: string, status: StreamStatus | null) {
  const next = new Map(held);
  if (status) next.set(item, status);
  else next.delete(item);
  held = next;
  listeners.forEach((listener) => listener());
}

/** Reports the status of an open live viewer's stream, by item, for as long as the viewer shows. */
export function useReportStatus(item: string, view: LiveView): void {
  const { state, text, anyLive } = statusOf(view);
  useEffect(() => report(item, { state, text, anyLive }), [item, state, text, anyLive]);
  useEffect(() => () => report(item, null), [item]);
}

/** The statuses the open live viewers reported, by item; a stream without a viewer showing has none. */
export function useStreamStatuses(): ReadonlyMap<string, StreamStatus> {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => held,
  );
}
