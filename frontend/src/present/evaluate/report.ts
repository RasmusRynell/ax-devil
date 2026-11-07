/** What an evaluation left out, for the lane's warnings. Nothing here is ever thrown. */
export class EvalReport {
  /**
   * Per mark (`MarkSpec.index`): rows left out because a field the mark needs was null or its lengths did not work.
   * A row is counted once, against its first failing mark.
   */
  skipped = new Uint32Array(0);
  /** Attribute columns the sample has in another kind than the catalog reads them in, read as null. */
  mismatched = 0;

  /** Zero the counters for a catalog of `marks` marks, keeping the memory. */
  clear(marks: number): void {
    if (this.skipped.length < marks)
      this.skipped = new Uint32Array(Math.max(marks, this.skipped.length * 2));
    else this.skipped.fill(0);
    this.mismatched = 0;
  }

  /** Marks skipped over all rows. */
  total(): number {
    let sum = 0;
    for (const count of this.skipped) sum += count;
    return sum;
  }
}
