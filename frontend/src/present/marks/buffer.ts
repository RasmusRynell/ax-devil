import { CodePoints } from "../expr/codePoints";

/** Words of one mark record; the layout is the table in `Rec`. */
export const WORDS = 12;

/** Word offsets inside a mark record. Words 2 to 5 and the `A`/`B` words are `f32` for some kinds (see the table). */
export const Rec = {
  /** `MarkPlan.index` of the mark. */
  Spec: 0,
  /** Entity row, or relation index with bit 31 set. */
  Row: 1,
  /** `f32` image-space geometry: the reference rect (left, top, right, bottom), or a line's x1, y1, x2, y2. */
  Geometry: 2,
  /** Stroke or text colour, packed `0xRRGGBBAA`. */
  Stroke: 6,
  /** Fill or label background, packed `0xRRGGBBAA`. */
  Fill: 7,
  /** `Flag` bits, and the placement candidate mask in bits 8 to 15. */
  Flags: 8,
  /** Offset of the mark's dynamic length numbers in `nums`. */
  Nums: 9,
  /** Text and label: first run. Polygon: first point. Bar: value (`f32`). Arrow: vx (`f32`). */
  A: 10,
  /** Text and label: run count. Polygon: point count. Arrow: vy (`f32`). */
  B: 11,
} as const;

/** Bits of the flags word. */
export const Flag = { Dash: 1, Stroke: 2, Fill: 4 } as const;

/** Set in `Rec.Row` when the mark belongs to a relation. */
export const RELATION_BIT = 0x8000_0000;

/** Words per run in `MarkBuffer.runs`: first code point, code point count, colour `0xRRGGBBAA`, weight. */
export const RUN_WORDS = 4;

/**
 * The rows that failed a check, keyed by a record's `Rec.Row` word, so everything such a row has is left out. Entity
 * rows and relations are separate rows. Clearing costs the faults, not the rows.
 */
export class RowFaults {
  private readonly flags = [new Uint8Array(256), new Uint8Array(256)];
  private touched = new Uint32Array(64);
  private touchedCount = 0;

  /** Whether any row failed since the last `clear`. */
  get any(): boolean {
    return this.touchedCount > 0;
  }

  /** Forget every fault, keeping the memory. */
  clear(): void {
    for (let i = 0; i < this.touchedCount; i++) {
      const word = this.touched[i];
      this.flags[word >>> 31][word & ~RELATION_BIT] = 0;
    }
    this.touchedCount = 0;
  }

  /** Whether the row of `word` has failed. */
  failed(word: number): boolean {
    if (this.touchedCount === 0) return false;
    const flags = this.flags[word >>> 31];
    const index = word & ~RELATION_BIT;
    return index < flags.length && flags[index] !== 0;
  }

  /** Mark the row of `word` as failed; true when it was not already. */
  fail(word: number): boolean {
    const which = word >>> 31;
    const index = word & ~RELATION_BIT;
    let flags = this.flags[which];
    if (index >= flags.length) {
      const grown = new Uint8Array(Math.max(flags.length * 2, index + 1));
      grown.set(flags);
      flags = this.flags[which] = grown;
    }
    if (flags[index] !== 0) return false;
    flags[index] = 1;
    if (this.touchedCount === this.touched.length) {
      const grown = new Uint32Array(this.touched.length * 2);
      grown.set(this.touched);
      this.touched = grown;
    }
    this.touched[this.touchedCount++] = word >>> 0;
    return true;
  }
}

/**
 * The marks of one lane's sample, evaluated and not yet laid out: records of `WORDS` words plus side arrays, all
 * growing by doubling and reused from frame to frame. Reserve before writing, then read the arrays again: a reserve
 * may replace them.
 */
export class MarkBuffer {
  words = new Uint32Array(WORDS * 256);
  f32 = new Float32Array(this.words.buffer);
  /** Records written. */
  count = 0;
  /** Dynamic length numbers: four per length (px, k, lo, hi). */
  nums = new Float64Array(1024);
  numsLength = 0;
  /** Runs: `RUN_WORDS` words each. */
  runs = new Uint32Array(RUN_WORDS * 256);
  /** A run's bar value, NaN for a text run. */
  runBars = new Float32Array(256);
  runCount = 0;
  /** The code points of every text run. */
  cps = new CodePoints();
  /** Per layer, the record count when the layer ended. */
  layerEnd = new Uint32Array(16);
  layerCount = 0;

  /** Empty it, keeping the memory. */
  clear(): void {
    this.count = 0;
    this.numsLength = 0;
    this.runCount = 0;
    this.cps.clear();
    this.layerCount = 0;
  }

  /** Make room for one more record and return its first word. */
  reserveRecord(): number {
    if ((this.count + 1) * WORDS > this.words.length) {
      const grown = new Uint32Array(this.words.length * 2);
      grown.set(this.words);
      this.words = grown;
      this.f32 = new Float32Array(grown.buffer);
    }
    return this.count * WORDS;
  }

  /** Make room for `extra` more numbers. */
  reserveNums(extra: number): void {
    if (this.numsLength + extra <= this.nums.length) return;
    const grown = new Float64Array(Math.max(this.nums.length * 2, this.numsLength + extra));
    grown.set(this.nums.subarray(0, this.numsLength));
    this.nums = grown;
  }

  /** Append a run; `bar` is NaN for text. Returns nothing: runs are addressed by `runCount` before the push. */
  pushRun(start: number, length: number, rgba: number, weight: number, bar: number): void {
    if ((this.runCount + 1) * RUN_WORDS > this.runs.length) {
      const grown = new Uint32Array(this.runs.length * 2);
      grown.set(this.runs);
      this.runs = grown;
      const bars = new Float32Array(this.runBars.length * 2);
      bars.set(this.runBars);
      this.runBars = bars;
    }
    const at = this.runCount * RUN_WORDS;
    this.runs[at] = start;
    this.runs[at + 1] = length;
    this.runs[at + 2] = rgba;
    this.runs[at + 3] = weight;
    this.runBars[this.runCount++] = bar;
  }

  /**
   * Leave out every record of a row in `faults`, keeping the order of the rest and the layer boundaries. The runs,
   * code points and numbers of the records left out stay behind unused: records address them by offset.
   */
  compact(faults: RowFaults): void {
    const words = this.words;
    let kept = 0;
    let at = 0;
    for (let layer = 0; layer < this.layerCount; layer++) {
      const end = this.layerEnd[layer];
      for (; at < end; at++) {
        if (faults.failed(words[at * WORDS + Rec.Row])) continue;
        if (kept !== at) words.copyWithin(kept * WORDS, at * WORDS, (at + 1) * WORDS);
        kept++;
      }
      this.layerEnd[layer] = kept;
    }
    this.count = kept;
  }

  /** Close layer `index`: the records so far belong to it or to earlier layers. */
  endLayer(index: number): void {
    if (index >= this.layerEnd.length) {
      const grown = new Uint32Array(Math.max(this.layerEnd.length * 2, index + 1));
      grown.set(this.layerEnd);
      this.layerEnd = grown;
    }
    this.layerEnd[index] = this.count;
    this.layerCount = index + 1;
  }
}
