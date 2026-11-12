/** Which frames a viewer asks the backend for: the window around the playhead, split by direction of travel. */

import type { Want } from "./protocol";

/** The frame half of a `Want`: the data window is sized by the session, which knows the lanes. */
export type FrameWant = Omit<Want, "data_ahead" | "data_behind">;

/** Share of the window ahead of the playhead. */
const AHEAD_SHARE = { playing: 0.85, paused: 0.5, backward: 0.15 };
/** Consecutive backward steps that start backward mode. */
const BACKWARD_STEPS = 2;

export class WantPlanner {
  #others = 0;
  #backwardSteps = 0;

  /** `windowSize` is the frame count the buffer holds: the playhead plus `ahead` plus `behind`. */
  constructor(windowSize: number) {
    this.resize(windowSize);
  }

  /** The buffer's frame count changed with its budget. The direction the viewer is travelling stays. */
  resize(windowSize: number): void {
    this.#others = Math.max(0, windowSize - 1);
  }

  /** Backward mode: it starts after two consecutive backward steps and ends on a forward step or play. */
  get backward(): boolean {
    return this.#backwardSteps >= BACKWARD_STEPS;
  }

  /** The playhead moved by a step. */
  stepped(delta: number): void {
    if (delta < 0) this.#backwardSteps++;
    else if (delta > 0) this.#backwardSteps = 0;
  }

  played(): void {
    this.#backwardSteps = 0;
  }

  /**
   * The `Want` for the current state. Call it once per scheduling tick: however many steps and seeks happened since
   * the last call, it names only the latest playhead.
   */
  want(state: { playhead: number; playing: boolean }): FrameWant {
    const backward = !state.playing && this.backward;
    const share = state.playing
      ? AHEAD_SHARE.playing
      : backward
        ? AHEAD_SHARE.backward
        : AHEAD_SHARE.paused;
    const ahead = Math.floor(this.#others * share);
    return {
      playhead: state.playhead,
      ahead,
      behind: this.#others - ahead,
      direction: backward ? "backward" : "forward",
    };
  }
}

/** Whether two `Want`s ask for the same: the viewer sends a new one only when it differs from the last. */
export const sameWant = (a: Want | undefined, b: Want): boolean =>
  a !== undefined &&
  a.playhead === b.playhead &&
  a.ahead === b.ahead &&
  a.behind === b.behind &&
  a.direction === b.direction &&
  a.data_ahead === b.data_ahead &&
  a.data_behind === b.data_behind;
