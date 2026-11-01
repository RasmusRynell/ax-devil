/** The overlays a viewer holds: per lane and frame the newest revision, exactly for the frames of the window. */

import { windowRange, type Window } from "./FrameBuffer";

/** What the buffer needs of a drawing: which text atlas generation it was prepared on. */
export interface Held {
  readonly generation: number;
}

type Entry<D> = { revision: number; drawing: D };

export class OverlayBuffer<D extends Held> {
  readonly #frameCount: number;
  readonly #lanes = new Map<number, Map<number, Entry<D>>>();
  #low = 0;
  #high = -1;

  constructor(frameCount: number) {
    this.#frameCount = frameCount;
  }

  /** Reports a new window and evicts every overlay outside it. Until the first window nothing is accepted. */
  setWindow(window: Window): void {
    ({ low: this.#low, high: this.#high } = windowRange(window, this.#frameCount));
    for (const overlays of this.#lanes.values())
      for (const sequenceId of overlays.keys())
        if (sequenceId < this.#low || sequenceId > this.#high) overlays.delete(sequenceId);
  }

  /**
   * Keeps an overlay that arrives inside the window unless a newer revision is held, and reports whether it was kept.
   * It replaces an older revision, and the same revision drawn on an older atlas generation (prepared again after
   * the atlas started a new page); the old drawing stays until then.
   */
  add(lane: number, sequenceId: number, revision: number, drawing: D): boolean {
    if (sequenceId < this.#low || sequenceId > this.#high) return false;
    let overlays = this.#lanes.get(lane);
    if (!overlays) {
      overlays = new Map();
      this.#lanes.set(lane, overlays);
    }
    const held = overlays.get(sequenceId);
    if (
      held &&
      (held.revision > revision ||
        (held.revision === revision && held.drawing.generation > drawing.generation))
    )
      return false;
    overlays.set(sequenceId, { revision, drawing });
    return true;
  }

  /** Whether the lane holds an overlay for the frame, of any revision. */
  has(lane: number, sequenceId: number): boolean {
    return this.#lanes.get(lane)?.has(sequenceId) ?? false;
  }

  get(lane: number, sequenceId: number): D | undefined {
    return this.#lanes.get(lane)?.get(sequenceId)?.drawing;
  }

  /** Drops everything a lane holds, as when it is hidden. */
  forgetLane(lane: number): void {
    this.#lanes.delete(lane);
  }
}
