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

/** Where a feed stands in plain words: `Reconnecting (2) — Unreachable`, `Failed — Login rejected`, `Live`. */
export function feedText(status: LiveFeedStatus): string {
  switch (status.status) {
    case "Connecting":
      return "Connecting…";
    case "Stalled":
      return `Stalled — No frames for ${status.seconds} s`;
    case "Reconnecting":
      return `Reconnecting (${status.attempt}) — ${reasonText(status.reason)}`;
    case "Failed":
      return `Failed — ${reasonText(status.reason)}`;
    case "Live":
      return "Live";
  }
}

/** The tone of a feed's tag: trouble is told apart, a feed that is up or on its way is not. */
export function feedTone(status: LiveFeedStatus): Tone {
  switch (status.status) {
    case "Live":
      return "success";
    case "Stalled":
    case "Reconnecting":
      return "caution";
    case "Failed":
      return "danger";
    case "Connecting":
      return "neutral";
  }
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
