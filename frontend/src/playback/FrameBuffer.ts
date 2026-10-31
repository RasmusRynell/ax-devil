/** The frames a viewer holds: exactly those inside the last reported window, within a byte budget. */

/** The window of sequence ids around the playhead that a `Want` reports. */
export type Window = { playhead: number; ahead: number; behind: number };

/**
 * Frames that fit the budget. The window size is deterministic per video, so the frontend never has to evict inside
 * its own window. At least one, so a budget smaller than a frame still shows something.
 */
export function windowSize(budgetBytes: number, frameBytes: number): number {
  return Math.max(1, Math.floor(budgetBytes / frameBytes));
}

export class FrameBuffer<F extends { readonly byteLength: number }> {
  readonly #budgetBytes: number;
  readonly #frameCount: number;
  readonly #frames = new Map<number, F>();
  #low = 0;
  #high = -1;
  #used = 0;

  constructor(budgetBytes: number, frameCount: number) {
    this.#budgetBytes = budgetBytes;
    this.#frameCount = frameCount;
  }

  get usedBytes(): number {
    return this.#used;
  }

  /** Reports a new window and evicts everything outside it. Until the first window nothing is accepted. */
  setWindow({ playhead, ahead, behind }: Window): void {
    this.#low = Math.max(0, playhead - behind);
    this.#high = Math.min(this.#frameCount - 1, playhead + ahead);
    for (const [sequenceId, frame] of this.#frames) {
      if (sequenceId >= this.#low && sequenceId <= this.#high) continue;
      this.#used -= frame.byteLength;
      this.#frames.delete(sequenceId);
    }
  }

  /** Keeps a frame that arrives inside the window and fits the budget; reports whether it was kept. */
  add(sequenceId: number, frame: F): boolean {
    if (!this.#inWindow(sequenceId)) return false;
    const used = this.#used - (this.#frames.get(sequenceId)?.byteLength ?? 0) + frame.byteLength;
    if (used > this.#budgetBytes) return false;
    this.#used = used;
    this.#frames.set(sequenceId, frame);
    return true;
  }

  /** Whether the frame is held, so it can be presented. */
  has(sequenceId: number): boolean {
    return this.#frames.has(sequenceId);
  }

  get(sequenceId: number): F | undefined {
    return this.#frames.get(sequenceId);
  }

  /** The highest held sequence id in `notBefore..atOrBefore`, if any. */
  newestPresentable(atOrBefore: number, notBefore: number): number | undefined {
    for (let id = Math.min(atOrBefore, this.#high); id >= Math.max(notBefore, this.#low); id--) {
      if (this.has(id)) return id;
    }
    return undefined;
  }

  #inWindow(sequenceId: number): boolean {
    return sequenceId >= this.#low && sequenceId <= this.#high;
  }
}
