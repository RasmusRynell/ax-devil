/** Which frames a viewer asks the backend for: the window around the playhead, split by direction of travel. */

import type { Want } from "./protocol";

/** Share of the window ahead of the playhead. */
const AHEAD_SHARE = { playing: 0.85, paused: 0.5, backward: 0.15 };
/** Consecutive backward steps that start backward mode. */
const BACKWARD_STEPS = 2;

export class WantPlanner {
  readonly #others: number;
  #backwardSteps = 0;

  /** `windowSize` is the frame count the buffer holds: the playhead plus `ahead` plus `behind`. */
  constructor(windowSize: number) {
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
  want(state: { playhead: number; playing: boolean }): Want {
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
