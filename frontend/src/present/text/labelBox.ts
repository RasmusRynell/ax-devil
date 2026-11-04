/**
 * Where a label goes: fed its runs' sizes, it finds its box and its anchored, pixel-snapped position. The box is the
 * runs' ink and, behind a background, the font's ascent and descent plus the padding, rounded up to device pixels, and it lands on whole
 * device pixels, as do the glyph origins, which keeps the atlas bitmaps crisp. One instance is reused for every label.
 */
export class LabelBox {
  /** Per run: advance (NaN when not shown), ink left, top, right, bottom, and offset from the first run, target pixels. */
  runs = new Float64Array(6 * 8);
  /** How many runs were added since `clear`. */
  count = 0;
  private shownRuns = 0;
  private ascent = 0;
  private descent = 0;
  /** Results of `place`: the box's top-left corner and size, the first run's left, and its baseline. */
  x = 0;
  y = 0;
  width = 0;
  height = 0;
  innerX = 0;
  baseline = 0;
  private relativeX = 0;
  private relativeBaseline = 0;

  clear(): void {
    this.count = 0;
    this.shownRuns = 0;
    this.ascent = 0;
    this.descent = 0;
  }

  /** Add a run. Ink is relative to the run's baseline origin; a run that is not shown takes no space. */
  add(
    shown: boolean,
    advance: number,
    left: number,
    top: number,
    right: number,
    bottom: number,
    ascent: number,
    descent: number,
  ): void {
    if (this.runs.length < (this.count + 1) * 6) {
      const grown = new Float64Array(this.runs.length * 2);
      grown.set(this.runs);
      this.runs = grown;
    }
    const runs = this.runs;
    const at = this.count * 6;
    runs[at] = shown ? advance : Number.NaN;
    runs[at + 1] = left;
    runs[at + 2] = top;
    runs[at + 3] = right;
    runs[at + 4] = bottom;
    this.count++;
    if (!shown) return;
    this.shownRuns++;
    this.ascent = Math.max(this.ascent, ascent);
    this.descent = Math.max(this.descent, descent);
  }

  /** The run's offset from the first run's origin, after `size`. */
  offset(run: number): number {
    return this.runs[run * 6 + 5];
  }

  /**
   * Work out the box size from the runs added. With `inkOnly` (nothing is painted behind the runs) the font's ascent
   * and descent are not reserved: the box spans the ink and the baseline origin. False when nothing is shown or the
   * size is not usable.
   */
  size(padX: number, padY: number, gap: number, dpr: number, inkOnly: boolean): boolean {
    if (this.shownRuns === 0) return false;
    const runs = this.runs;
    let running = 0;
    for (let run = 0; run < this.count; run++) {
      runs[run * 6 + 5] = running;
      if (runs[run * 6] === runs[run * 6]) running += runs[run * 6] + gap;
    }
    const ascent = inkOnly ? 0 : this.ascent;
    let left = 0;
    let right = running - gap;
    let top = -ascent;
    let bottom = inkOnly ? 0 : this.descent;
    for (let run = 0; run < this.count; run++) {
      const at = run * 6;
      if (runs[at] !== runs[at]) continue;
      left = Math.min(left, runs[at + 5] + runs[at + 1]);
      top = Math.min(top, runs[at + 2]);
      right = Math.max(right, runs[at + 5] + runs[at + 3]);
      bottom = Math.max(bottom, runs[at + 4]);
    }
    // Device pixels covered, rounded up; a hair of tolerance keeps float noise from adding a pixel.
    const width = Math.ceil((right - left + 2 * padX) * dpr - 1e-3) / dpr;
    const height = Math.ceil((bottom - top + 2 * padY) * dpr - 1e-3) / dpr;
    if (!(width > 0 && height > 0 && width * dpr < 2 ** 24 && height * dpr < 2 ** 24)) return false;
    this.width = width;
    this.height = height;
    this.relativeX = padX - left;
    this.relativeBaseline = padY - top;
    return true;
  }

  /** Put the box with the anchor factors (`ax`, `ay`: 0, 0.5 or 1 across and down) at (`px`, `py`), snapped to device pixels. */
  place(px: number, py: number, ax: number, ay: number, dpr: number): boolean {
    const exactX = px - ax * this.width;
    const exactY = py - ay * this.height;
    if (!Number.isFinite(exactX) || !Number.isFinite(exactY)) return false;
    this.x = Math.round(exactX * dpr) / dpr;
    this.y = Math.round(exactY * dpr) / dpr;
    this.innerX = this.x + this.relativeX;
    this.baseline = this.y + this.relativeBaseline;
    return true;
  }
}
