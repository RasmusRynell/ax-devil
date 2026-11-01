import type { SampleView } from "../columns";
import { Flag, MarkBuffer, RELATION_BIT, Rec } from "../marks/buffer";
import { Kind, Len, type MarkPlan } from "../marks/plan";
import type { EvalReport } from "./report";

/** A bar value clamped to 0..1 and rounded to hundredths with ties to even, as the reference's `{:.2}` did. */
export function hundredths(value: number): number {
  const scaled = Math.min(1, Math.max(0, value)) * 100;
  const floor = Math.floor(scaled);
  const rest = scaled - floor;
  const rounded = rest > 0.5 || (rest === 0.5 && floor % 2 === 1) ? floor + 1 : floor;
  return rounded / 100;
}

/** How far making a mark's text got. */
const Made = { Ok: 0, Fault: 1, Nothing: 2 } as const;
type Made = (typeof Made)[keyof typeof Made];

/** Writes the marks of one sample into a buffer, one row of one mark at a time. */
export class MarkWriter {
  private cols!: SampleView;
  private out!: MarkBuffer;
  private report!: EvalReport;

  /** Point at the sample and the buffer for the marks that follow. */
  start(cols: SampleView, out: MarkBuffer, report: EvalReport): void {
    this.cols = cols;
    this.out = out;
    this.report = report;
  }

  /** Evaluate `plan` for a row (an entity row, or a relation index when `relation`) and append its record. */
  write(plan: MarkPlan, row: number, relation: boolean): void {
    if (plan.never || (plan.when !== null && plan.when.fn(row) !== 1)) return;
    const { cols, out } = this;
    const kind = plan.kind;
    const polygonFirst = kind === Kind.Polygon ? cols.polyIndex[2 * row] : 0;
    const polygonCount = kind === Kind.Polygon ? cols.polyIndex[2 * row + 1] : 0;
    if (kind === Kind.Polygon && polygonCount === 0) return;
    let vx = 0;
    let vy = 0;
    if (kind === Kind.Arrow) {
      vx = plan.vx ? plan.vx.fn(row) : cols.velocity[2 * row];
      vy = plan.vy ? plan.vy.fn(row) : cols.velocity[2 * row + 1];
      // No velocity, no arrow: that is not a fault.
      if (!(vx === vx && vy === vy)) return;
    }
    let mask = 0;
    for (let i = 0; i < plan.place.length; i++) {
      const when = plan.place[i].when;
      if (when === null || when.fn(row) === 1) mask |= 1 << i;
    }
    if (plan.place.length > 0 && mask === 0) return;

    const base = out.reserveRecord();
    const cpsMark = out.cps.length;
    const runMark = out.runCount;
    const numsMark = out.numsLength;
    const words = out.words;
    const f32 = out.f32;
    words[base + Rec.Spec] = plan.index;
    words[base + Rec.Row] = relation ? (row | RELATION_BIT) >>> 0 : row;
    if (!this.geometry(plan, row, base)) return this.skip(plan, cpsMark, runMark, numsMark);

    let flags = mask << 8;
    if (plan.stroke) {
      const v = plan.stroke.fn(row);
      if (v === v) {
        words[base + Rec.Stroke] = v >>> 0;
        flags |= Flag.Stroke;
        if (plan.dash !== null && plan.dash.fn(row) === 1) flags |= Flag.Dash;
      }
    }
    if (plan.fill) {
      const v = plan.fill.fn(row);
      if (v === v) {
        words[base + Rec.Fill] = v >>> 0;
        flags |= Flag.Fill;
      }
    }
    words[base + Rec.Flags] = flags;
    words[base + Rec.A] = 0;
    words[base + Rec.B] = 0;
    let ok = true;
    switch (kind) {
      case Kind.Arrow:
        ok = (flags & Flag.Stroke) !== 0;
        f32[base + Rec.A] = vx;
        f32[base + Rec.B] = vy;
        break;
      case Kind.Polygon:
        words[base + Rec.A] = polygonFirst;
        words[base + Rec.B] = polygonCount;
        break;
      case Kind.Bar: {
        const value = plan.value ? plan.value.fn(row) : Number.NaN;
        ok = value === value;
        f32[base + Rec.A] = value;
        break;
      }
      case Kind.Text: {
        const made = this.textRun(plan, row, base);
        if (made === Made.Nothing) return this.drop(cpsMark, runMark, numsMark);
        ok = made === Made.Ok;
        break;
      }
      case Kind.Label:
        ok = this.runs(plan, row, base);
        if (ok && words[base + Rec.B] === 0) return this.drop(cpsMark, runMark, numsMark);
        break;
      case Kind.Circle:
        // The radius is required; the layout checks it, the evaluator only needs it to exist.
        ok = plan.len[Len.R].present;
        break;
      default:
        break;
    }
    if (!ok || !this.lengths(plan, row, base, numsMark))
      return this.skip(plan, cpsMark, runMark, numsMark);
    out.count++;
  }

  /** The reference rect (or a line's ends) into words 2 to 5; false when a part is null. */
  private geometry(plan: MarkPlan, row: number, base: number): boolean {
    const f32 = this.out.f32;
    const at = base + Rec.Geometry;
    if (plan.kind === Kind.Line) {
      if (!plan.from || !plan.to) return false;
      f32[at] = plan.from.x(row);
      f32[at + 1] = plan.from.y(row);
      f32[at + 2] = plan.to.x(row);
      f32[at + 3] = plan.to.y(row);
    } else if (plan.on) {
      f32[at] = plan.on.l(row);
      f32[at + 1] = plan.on.t(row);
      f32[at + 2] = plan.on.r(row);
      f32[at + 3] = plan.on.b(row);
    } else {
      const box = this.cols.box;
      for (let i = 0; i < 4; i++) f32[at + i] = box[4 * row + i];
    }
    return (
      f32[at] === f32[at] &&
      f32[at + 1] === f32[at + 1] &&
      f32[at + 2] === f32[at + 2] &&
      f32[at + 3] === f32[at + 3]
    );
  }

  /** The text of a `text` mark as its only run: a fault when its text or colour is null, nothing when it is empty. */
  private textRun(plan: MarkPlan, row: number, base: number): Made {
    const out = this.out;
    const start = out.cps.length;
    if (!plan.text || !plan.text(row, out.cps)) return Made.Fault;
    const length = out.cps.length - start;
    if (length === 0) return Made.Nothing;
    const color = plan.color ? plan.color.fn(row) : Number.NaN;
    if (!(color === color)) return Made.Fault;
    out.words[base + Rec.A] = out.runCount;
    out.words[base + Rec.B] = 1;
    out.pushRun(start, length, color >>> 0, 0, Number.NaN);
    return Made.Ok;
  }

  /** The runs of a label that have something to show; false when a shown run has no colour. */
  private runs(plan: MarkPlan, row: number, base: number): boolean {
    const out = this.out;
    const first = out.runCount;
    for (const run of plan.runs) {
      const start = out.cps.length;
      let bar = Number.NaN;
      if (run.text) {
        if (!run.text(row, out.cps) || out.cps.length === start) {
          out.cps.length = start;
          continue;
        }
      } else {
        const value = run.bar ? run.bar.fn(row) : Number.NaN;
        if (!(value === value)) continue;
        bar = hundredths(value);
      }
      const color = run.color.fn(row);
      if (!(color === color)) return false;
      out.pushRun(start, out.cps.length - start, color >>> 0, run.weight, bar);
    }
    out.words[base + Rec.A] = first;
    out.words[base + Rec.B] = out.runCount - first;
    if (plan.background) {
      const v = plan.background.fn(row);
      if (v === v) {
        out.words[base + Rec.Fill] = v >>> 0;
        out.words[base + Rec.Flags] |= Flag.Fill;
      }
    }
    return true;
  }

  /** The dynamic lengths into `nums`; false when one of their numbers is null. */
  private lengths(plan: MarkPlan, row: number, base: number, numsMark: number): boolean {
    const out = this.out;
    if (plan.numWords === 0) {
      out.words[base + Rec.Nums] = 0xffff_ffff;
      return true;
    }
    out.reserveNums(plan.numWords);
    const nums = out.nums;
    out.words[base + Rec.Nums] = numsMark;
    for (const length of plan.dynamic) {
      const parts = length.parts as NonNullable<typeof length.parts>;
      const at = numsMark + length.offset;
      for (let i = 0; i < 4; i++) {
        const v = parts[i].constant ?? parts[i].fn(row);
        if (!(v === v)) return false;
        nums[at + i] = v;
      }
    }
    out.numsLength = numsMark + plan.numWords;
    return true;
  }

  private skip(plan: MarkPlan, cps: number, runs: number, nums: number): void {
    this.report.skipped[plan.index]++;
    this.drop(cps, runs, nums);
  }

  private drop(cps: number, runs: number, nums: number): void {
    const out = this.out;
    out.cps.length = cps;
    out.runCount = runs;
    out.numsLength = nums;
  }
}
