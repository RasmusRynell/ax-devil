/**
 * Estimates the backend's clock from echoed time-sync requests. Every time here is in microseconds; callers convert
 * `performance.now()` milliseconds by multiplying by 1000.
 */

/** One round trip: the local send and receive times and the server's time when it answered. */
export interface TimeSample {
  sentUs: number;
  receivedUs: number;
  serverUs: number;
}

/** How many recent samples are kept; the lowest round trip among them wins. */
const WINDOW = 8;

/** The server-minus-local offset of the lowest round-trip sample among the most recent ones. */
export class OffsetEstimator {
  readonly #samples: TimeSample[] = [];

  add(sample: TimeSample): void {
    this.#samples.push(sample);
    if (this.#samples.length > WINDOW) this.#samples.shift();
  }

  /** Server time now, from `performance.now()` milliseconds; undefined before the first sample. */
  serverNowUs(performanceNowMs: number): number | undefined {
    let best: TimeSample | undefined;
    for (const sample of this.#samples) {
      if (!best || sample.receivedUs - sample.sentUs < best.receivedUs - best.sentUs) best = sample;
    }
    if (!best) return undefined;
    return performanceNowMs * 1000 + best.serverUs - (best.sentUs + best.receivedUs) / 2;
  }
}
