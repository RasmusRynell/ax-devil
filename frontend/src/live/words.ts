/** What a live viewer says of its feeds and times: the wire carries codes, and this is the one place that words them. */

import type { LiveFeedStatus, LiveReason } from "../generated/api";
import type { Tone } from "../kit/look";

/** A short reason for each code. Never the device's own text, which the backend does not send. */
const REASONS: Record<LiveReason, string> = {
  LoginRequired: "Login required",
  LoginRejected: "Login rejected",
  Forbidden: "Not allowed",
  NotAxis: "Not an Axis camera",
  NotFound: "Not found",
  Unsupported: "Unsupported",
  BrokerUnsupportedProtocol: "Broker protocol unsupported",
  BrokerClientIdRejected: "Broker rejected the client ID",
  BrokerBadCredentials: "Broker login rejected",
  BrokerNotAuthorized: "Broker not authorized",
  Busy: "Camera's MQTT client in use",
  Unrestorable: "Camera settings not restorable",
  InvalidTarget: "Invalid address",
  Unreachable: "Unreachable",
  TimedOut: "Timed out",
  Lost: "Connection lost",
  Silent: "No data",
  Malformed: "Unreadable data",
  Throttled: "Camera is limiting requests",
  DeviceFault: "Camera error",
};

/** The reason a feed is not live, in words. */
export const reasonText = (reason: LiveReason): string => REASONS[reason];

type FeedState = LiveFeedStatus["status"];

/** Each state of a feed as the viewer words it: its label, what it means, its tone, and whether it is a problem. */
const STATES: Record<
  FeedState,
  { label: string; description: string; tone: Tone; problem: boolean }
> = {
  Connecting: {
    label: "Connecting…",
    description: "Waiting for the device to answer.",
    tone: "caution",
    problem: false,
  },
  Live: {
    label: "● Live",
    description: "Connected to the device.",
    tone: "success",
    problem: false,
  },
  Stalled: {
    label: "Stalled",
    description: "Connected, but no data has arrived recently.",
    tone: "caution",
    problem: true,
  },
  Reconnecting: {
    label: "Reconnecting",
    description: "The connection failed; the source is trying again.",
    tone: "caution",
    problem: true,
  },
  Failed: {
    label: "✕ Failed",
    description: "The connection failed and is not retried automatically.",
    tone: "danger",
    problem: true,
  },
};

/** What a feed's state means, for its tooltip. */
export const describe = (status: LiveFeedStatus): string => STATES[status.status].description;

/** The tone of a feed's tag: trouble is told apart, a feed that is up or on its way is not. */
export const toneOf = (status: LiveFeedStatus): Tone => STATES[status.status].tone;

/** A feed's short state text, with the attempt while it reconnects: `Reconnecting (2)`, `● Live`. */
export function headline(status: LiveFeedStatus): string {
  const { label } = STATES[status.status];
  return status.status === "Reconnecting" ? `${label} (${status.attempt})` : label;
}

/** The line a problem gets under the bar, `Video: Failed — Login rejected`; `null` for a feed that is not in trouble. */
export function problemLine(feed: string, status: LiveFeedStatus): string | null {
  const { description, problem } = STATES[status.status];
  if (!problem) return null;
  const reason =
    status.status === "Stalled"
      ? `No frames for ${status.seconds} s`
      : "reason" in status
        ? reasonText(status.reason)
        : description;
  return `${feed}: ${headline(status)} — ${reason}`;
}

/** Whether a feed wants the user: it is stuck, or failed, and Retry reopens it. */
export const needsRetry = (status: LiveFeedStatus | null): boolean =>
  status?.status === "Failed" || status?.status === "Stalled";

const DAY_US = 86_400_000_000;

const pad = (value: number, width: number): string => String(value).padStart(width, "0");

/** A time in microseconds since the Unix epoch as the UTC time of day, `HH:MM:SS.ffffff`. */
export function utcTime(us: number): string {
  const day = ((Math.floor(us) % DAY_US) + DAY_US) % DAY_US;
  const seconds = Math.floor(day / 1_000_000);
  return `${pad(Math.floor(seconds / 3600), 2)}:${pad(Math.floor(seconds / 60) % 60, 2)}:${pad(seconds % 60, 2)}.${pad(day % 1_000_000, 6)}`;
}
