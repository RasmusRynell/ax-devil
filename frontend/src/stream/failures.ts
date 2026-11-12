/** The frames the backend could not decode, with why. */
export class FrameFailures {
  readonly #reasons = new Map<number, string>();
  /** The ids in `#reasons`, ascending, to find the latest one at or before a position without a scan. */
  #ids: number[] = [];

  /** Records a failed frame; a later reason for the same frame replaces the earlier. */
  note(sequenceId: number, reason: string): void {
    if (!this.#reasons.has(sequenceId)) {
      this.#ids.push(sequenceId);
      this.#ids.sort((a, b) => a - b);
    }
    this.#reasons.set(sequenceId, reason);
  }

  clear(): void {
    this.#reasons.clear();
    this.#ids = [];
  }

  /** The failed frame with the highest sequence id at or before `position`, by binary search. */
  latestAtOrBefore(position: number): { sequenceId: number; reason: string } | undefined {
    const ids = this.#ids;
    let low = 0;
    let high = ids.length;
    while (low < high) {
      const middle = (low + high) >> 1;
      if (ids[middle] <= position) low = middle + 1;
      else high = middle;
    }
    const sequenceId = ids[low - 1];
    return sequenceId === undefined
      ? undefined
      : { sequenceId, reason: this.#reasons.get(sequenceId) ?? "" };
  }
}
