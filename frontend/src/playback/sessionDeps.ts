/** What a playback session is given, and what it reports of the clock. */

import type { Connect } from "./wire";

/** What one viewer may hold of decoded frames: it divides into the window the backend is asked to fill. */
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
  /** Where an earlier session of this video left off: this one opens there, paused, at that speed. */
  resume?: ResumeState;
  /** Told what the clock is doing after every change the viewer makes: the intent a presenter publishes. */
  onIntent?: (intent: PlaybackIntent) => void;
}

/** What the viewer asked the clock to do: the position it is on and whether it runs, at what speed. */
export type PlaybackIntent = { frame: number; playing: boolean; speed: number };

/** What carries a video's viewing from one session to the next, across a reconnection or a viewer shown again. */
export type ResumeState = { frame: number; speed: number };
