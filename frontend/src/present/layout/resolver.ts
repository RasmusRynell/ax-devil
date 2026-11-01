import { Basis } from "../expr/compiled";
import type { LengthPlan } from "../marks/plan";

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

  /** The displayed image, and the UI zoom to cancel. */
  view(width: number, height: number, zoom: number): void {
    this.imageW = width;
    this.imageH = height;
    this.imageMin = Math.min(width, height);
    this.unzoom = 1 / zoom;
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
