/** What a playback session is given, and what it reports of the clock. */

import type { LaneSettings } from "./contract";
import type { Timing } from "./lanes";
import type { LanePolicyWire } from "./protocol";
import type { Connect } from "./wire";

/** What one window may hold of decoded frames, shared by its panes (see `budget.ts`); the most one viewer ever holds. */
export const BUFFER_BUDGET_BYTES = 512 * 1024 * 1024;

export interface SessionDeps {
  /** A ticket for one socket: the first message of every connection spends one. Rejects when it cannot get one. */
  ticket: () => Promise<string>;
  connect: Connect;
  /** Monotonic milliseconds. */
  now: () => number;
  /** Calls back once at the next display refresh; returns the way to cancel. */
  frame: (callback: () => void) => () => void;
  /** Calls back once after `ms`; returns the way to cancel. */
  after: (callback: () => void, ms: number) => () => void;
  budgetBytes?: number;
  /** The Personal default catalog id, or `null` for the built-in default; read when the lanes open. */
  defaultCatalog?: () => string | null;
  /** The Personal default timing new lanes start with; read when the lanes open. */
  defaultTiming?: () => Timing;
  /** The lane settings of the entry before this one, by lane position: this entry's lanes start from them. */
  lanes?: readonly LaneSettings[];
  /** Plays from the first frame once it is shown. For an ordinary open only: a resumed or followed one stays as it was. */
  autoplay?: boolean;
  /** Where an earlier session left off and the speed it ran at: this one opens there, paused, at that speed. A missing frame opens at the start. */
  resume?: Partial<ResumeState>;
  /** Told the policy of a lane, by its slot, after the viewer changed it: the workspace keeps it with the recipe. */
  onPolicy?: (slot: string, policy: LanePolicyWire) => void;
  /** Told what the clock is doing after every change the viewer makes: the intent a presenter publishes. */
  onIntent?: (intent: PlaybackIntent) => void;
}

/** What the viewer asked the clock to do: the position it is on and whether it runs, at what speed. */
export type PlaybackIntent = { frame: number; playing: boolean; speed: number };

/** What carries a video's viewing from one session to the next, across a reconnection or a viewer shown again. */
export type ResumeState = { frame: number; speed: number };

/** Waits between reconnection attempts; the last one repeats. */
export const RETRY_MS = [500, 1000, 2000, 4000, 8000];
/**
 * Close codes that end a stream for good, with what the viewer says; other closes are retried.
 */
export const ENDED: Record<number, string> = {
  4401: "The backend refused this video stream.",
  4403: "This video stream was ended: access to it was withdrawn.",
};
