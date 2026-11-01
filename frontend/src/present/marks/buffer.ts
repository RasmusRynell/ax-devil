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
