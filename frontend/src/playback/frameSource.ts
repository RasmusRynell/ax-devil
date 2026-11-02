/** When the clock may show a frame: the video is held, and the lanes have their data or have waited long enough. */

import type { FrameSource } from "./clock";

/** How long a frame shown after a seek or step waits for lanes that still lack data. */
export const PENDING_MS = 250;

/**
 * The clock's view of what can be shown. Playing never holds the video back for data: a lane without data shows a
 * loading state. A paused clock waits for the frame it was moved to until every lane has its data, or `PENDING_MS` have
 * passed since `waitingSince()`.
 */
export function displaySource(
  video: {
    has(sequenceId: number): boolean;
    newestPresentable(atOrBefore: number, notBefore: number): number | undefined;
  },
  lanes: { ready(sequenceId: number): boolean },
  waitingSince: () => number,
  now: () => number,
): FrameSource {
  return {
    has: (id) => video.has(id) && (lanes.ready(id) || now() - waitingSince() >= PENDING_MS),
    newestPresentable: (atOrBefore, notBefore) => video.newestPresentable(atOrBefore, notBefore),
  };
}

/** The wait of a paused clock for lane data: when it started, and the timer that ends it. */
export class DataWait {
  readonly #now: () => number;
  readonly #after: (callback: () => void, ms: number) => () => void;
  readonly #wake: () => void;
  #since = 0;
  #cancel: (() => void) | undefined;

  /** `wake` asks for another display refresh, which finds the wait over. */
  constructor(
    now: () => number,
    after: (callback: () => void, ms: number) => () => void,
    wake: () => void,
  ) {
    this.#now = now;
    this.#after = after;
    this.#wake = wake;
  }

  /** When the frame now asked for was asked for. */
  since = (): number => this.#since;

  /** A frame is asked for: its wait starts now. */
  restart(): void {
    this.#since = this.#now();
    this.stop();
  }

  /** Wakes the clock when the wait is over, unless that is already set up or it is over. */
  hold(): void {
    const left = PENDING_MS - (this.#now() - this.#since);
    if (this.#cancel || left <= 0) return;
    this.#cancel = this.#after(() => {
      this.#cancel = undefined;
      this.#wake();
    }, left);
  }

  stop(): void {
    this.#cancel?.();
    this.#cancel = undefined;
  }
}
