import { Basis } from "../expr/compiled";
import { LEN_COUNT, type LengthPlan, type MarkPlan, type Program } from "../marks/plan";

/** Resolved lengths kept per mark: its `Len`s, then `dx` and `dy` of each candidate placement (a mask holds at most 8). */
const STRIDE = LEN_COUNT + 16;

/** Turns lengths into CSS pixels for the record being laid out. */
export class Resolver {
  nums: Float64Array = new Float64Array(0);
  base = 0;
  /** 1 divided by the UI zoom. */
  unzoom = 1;
  boxW = 0;
  boxH = 0;
  boxMin = 0;
  imageW = 0;
  imageH = 0;
  imageMin = 0;
  /** Per mark and `Len`, the length as `length` would give it if the mark's own number fixes it, else NaN. */
  private fixed = new Float64Array(0);
  private program: Program | null = null;

  /** The displayed image, and the UI zoom to cancel; the lengths `program` fixes are resolved again when the zoom changed. */
  view(width: number, height: number, zoom: number, program: Program): void {
    this.imageW = width;
    this.imageH = height;
    this.imageMin = Math.min(width, height);
    const unzoom = 1 / zoom;
    if (program === this.program && unzoom === this.unzoom) return;
    this.program = program;
    this.unzoom = unzoom;
    const marks = program.marks;
    this.fixed = new Float64Array(marks.length * STRIDE);
    for (const mark of marks) {
      const plans = [...mark.len, ...mark.place.flatMap((entry) => [entry.dx, entry.dy])];
      plans.forEach((plan, i) => {
        this.fixed[mark.index * STRIDE + i] =
          plan.fixed === plan.fixed ? this.length(plan) : Number.NaN;
      });
    }
  }

  /** Length `i` (a `Len`) of `mark`; a plain table read when the mark's own numbers fix it. */
  of(mark: MarkPlan, i: number): number {
    const value = this.fixed[mark.index * STRIDE + i];
    return value === value ? value : this.length(mark.len[i]);
  }

  /** The `dx` (axis 0) or `dy` (axis 1) of candidate placement `i` of `mark`. */
  ofPlace(mark: MarkPlan, i: number, axis: number): number {
    const value = this.fixed[mark.index * STRIDE + LEN_COUNT + 2 * i + axis];
    if (value === value) return value;
    const entry = mark.place[i];
    return this.length(axis === 0 ? entry.dx : entry.dy);
  }

  /** The reference rect's size, which `box.*` lengths of the next record are relative to. */
  box(width: number, height: number): void {
    this.boxW = width;
    this.boxH = height;
    this.boxMin = Math.min(width, height);
  }

  /** `clamp(px + k * basis, lo, hi)` with the absolute parts divided by the zoom. NaN when a part is NaN. */
  length(plan: LengthPlan): number {
    if (plan.fixed === plan.fixed) return plan.zoomed ? plan.fixed * this.unzoom : plan.fixed;
    let px = plan.px;
    let k = plan.k;
    let lo = plan.lo;
    let hi = plan.hi;
    if (plan.offset >= 0) {
      const at = this.base + plan.offset;
      px = this.nums[at];
      k = this.nums[at + 1];
      lo = this.nums[at + 2];
      hi = this.nums[at + 3];
    }
    const z = plan.zoomed ? this.unzoom : 1;
    let v = px * z;
    switch (plan.basis) {
      case Basis.None:
        break;
      case Basis.BoxW:
        v += k * this.boxW;
        break;
      case Basis.BoxH:
        v += k * this.boxH;
        break;
      case Basis.BoxMin:
        v += k * this.boxMin;
        break;
      case Basis.ImageW:
        v += k * this.imageW;
        break;
      case Basis.ImageH:
        v += k * this.imageH;
        break;
      case Basis.ImageMin:
        v += k * this.imageMin;
        break;
    }
    return Math.min(Math.max(v, lo * z), hi * z);
  }
}
