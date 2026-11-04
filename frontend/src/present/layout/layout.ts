import type { CompiledCatalog } from "../catalog/types";
import type { SampleView } from "../columns";
import type { EvalReport } from "../evaluate/report";
import { Flag, type MarkBuffer, Rec, RowFaults } from "../marks/buffer";
import { Kind, Len, type MarkPlan, programOf } from "../marks/plan";
import { AtlasFull, type TextSource } from "../text/source";
import { Labels } from "./labels";
import { Resolver } from "./resolver";
import { DISCARD, type LayoutView, type PrimitiveSink, type Region } from "./sink";

/** A pinned rect or a plain one, in target pixels. */
interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Pixels past a mark's geometry that its quads still cover: the edge antialiasing and snapping to device pixels. */
const EDGE_SLACK = 2;

/** The lengths that can move a box, bar or circle outside its reference rect. */
const BOX_REACH = [
  Len.W,
  Len.H,
  Len.Dx,
  Len.Dy,
  Len.Outset,
  Len.R,
  Len.MarginTop,
  Len.MarginRight,
  Len.MarginBottom,
  Len.MarginLeft,
];

/**
 * Turns the evaluated marks of a lane into primitives for a displayed image size: resolves lengths, pins and outsets
 * rects, places labels, measures text. One instance per lane and text source; its scratch memory is reused.
 */
export class Layouter {
  private readonly sizes = new Resolver();
  private readonly labels: Labels;
  private readonly rect: Rect = { x: 0, y: 0, w: 0, h: 0 };
  private readonly head = new Float32Array(6);
  private report: EvalReport | null = null;
  /** The rows with a mark whose lengths do not work at this view; they are cleared by every `run`, never kept in the buffer. */
  private readonly faults = new RowFaults();
  /** A row failed during the pass, so what it already drew must be taken back by another pass. */
  private again = false;

  constructor(private readonly text: TextSource) {
    this.labels = new Labels(text, this.sizes);
  }

  /**
   * Lay out `marks` and write the result to `sink`. When the atlas page fills up it is reset and the layout runs
   * once more on the fresh page, leaving out the glyphs that still do not fit. A mark whose lengths do not work
   * leaves out every mark of its entity row or relation in every layer, as evaluation does, and the row is counted
   * once in `report` when given.
   */
  run(
    marks: MarkBuffer,
    catalog: CompiledCatalog,
    cols: SampleView,
    view: LayoutView,
    sink: PrimitiveSink,
    report: EvalReport | null = null,
  ): void {
    this.report = report;
    this.faults.clear();
    try {
      this.passes(marks, catalog, cols, view, sink);
    } catch (error) {
      if (!(error instanceof AtlasFull)) throw error;
      const text = this.text;
      const dropping = text.dropOverflow;
      text.reset();
      text.dropOverflow = true;
      try {
        // Rows that failed before the page filled up stay failed and are not counted again.
        this.passes(marks, catalog, cols, view, sink);
      } finally {
        text.dropOverflow = dropping;
        // Runs measured while glyphs were dropped lack some; the next pass measures them again.
        this.labels.discardRuns();
      }
    }
  }

  /** One pass, and a second one when a row failed in it, so the second leaves out what the failed row drew before. */
  private passes(
    marks: MarkBuffer,
    catalog: CompiledCatalog,
    cols: SampleView,
    view: LayoutView,
    sink: PrimitiveSink,
  ): void {
    this.again = false;
    this.pass(marks, catalog, cols, view, sink);
    if (!this.again) return;
    this.again = false;
    this.pass(marks, catalog, cols, view, sink);
  }

  private pass(
    marks: MarkBuffer,
    catalog: CompiledCatalog,
    cols: SampleView,
    view: LayoutView,
    sink: PrimitiveSink,
  ): void {
    const program = programOf(catalog);
    this.labels.begin(program);
    this.sizes.view(view.width, view.height, view.zoom, program);
    this.sizes.nums = marks.nums;
    sink.begin();
    let at = 0;
    for (let layer = 0; layer < marks.layerCount; layer++) {
      const end = marks.layerEnd[layer];
      for (; at < end; at++)
        this.record(program.marks[marks.words[at * 12 + Rec.Spec]], marks, at, cols, view, sink);
      sink.endLayer(layer);
    }
  }

  private record(
    plan: MarkPlan,
    marks: MarkBuffer,
    record: number,
    cols: SampleView,
    view: LayoutView,
    sink: PrimitiveSink,
  ): void {
    const base = record * 12;
    const word = marks.words[base + Rec.Row];
    if (this.faults.failed(word)) return;
    const { width, height } = view;
    const f32 = marks.f32;
    const sizes = this.sizes;
    sizes.base = marks.words[base + Rec.Nums];
    const l = f32[base + Rec.Geometry] * width;
    const t = f32[base + Rec.Geometry + 1] * height;
    const r = f32[base + Rec.Geometry + 2] * width;
    const b = f32[base + Rec.Geometry + 3] * height;
    // A line has no reference rect; its bounding box stands in for the gate and for `box.*` lengths.
    if (plan.kind === Kind.Line) sizes.box(Math.abs(r - l), Math.abs(b - t));
    else sizes.box(r - l, b - t);
    if (plan.len[Len.MinSize].present && !(sizes.boxMin > sizes.of(plan, Len.MinSize))) return;
    const visible = view.visible;
    // A mark outside the window is still checked, so a row's failure does not depend on what is on screen.
    const culled = !!visible && this.outside(plan, marks, record, l, t, r, b, visible, view.dpr);
    const out = culled ? DISCARD : sink;
    let ok: boolean;
    switch (plan.kind) {
      case Kind.Box:
        ok = this.box(plan, marks, base, l, t, r, b, out);
        break;
      case Kind.Circle:
        ok = this.circle(plan, marks, base, l, t, r, b, out);
        break;
      case Kind.Bar:
        ok = this.bar(plan, marks, base, l, t, r, b, out);
        break;
      case Kind.Line:
        ok = this.line(plan, marks, base, l, t, r, b, out);
        break;
      case Kind.Arrow:
        ok = this.arrow(plan, marks, base, l, t, r, b, view, out);
        break;
      case Kind.Polygon:
        ok = this.polygon(plan, marks, base, cols, view, out);
        break;
      default:
        ok = culled
          ? this.labels.usable(plan)
          : this.labels.draw(plan, marks, record, l, t, r, b, view, sink);
        break;
    }
    if (ok) return;
    if (this.faults.fail(word) && this.report) this.report.skipped[plan.index]++;
    this.again = true;
  }

  /**
   * Whether nothing of the record can reach `visible`: its reference rect (`l`, `t`, `r`, `b`) grown by everything that
   * can push the mark outside it, summed without regard to sign so the bound is safe. False when a length is unusable,
   * so the layout reports it, and for polygons, whose extent is not worth finding out.
   */
  private outside(
    plan: MarkPlan,
    marks: MarkBuffer,
    record: number,
    l: number,
    t: number,
    r: number,
    b: number,
    visible: Region,
    dpr: number,
  ): boolean {
    const sizes = this.sizes;
    let reach: number;
    switch (plan.kind) {
      case Kind.Polygon:
        return false;
      case Kind.Text:
      case Kind.Label:
        reach = this.labels.reach(plan, marks, record, dpr);
        break;
      case Kind.Arrow:
        reach = sizes.of(plan, Len.Max) + sizes.of(plan, Len.Head) + sizes.of(plan, Len.HeadWidth);
        break;
      default:
        reach = 0;
        for (const i of BOX_REACH) reach += Math.abs(sizes.of(plan, i));
        break;
    }
    reach += sizes.of(plan, Len.Width) + EDGE_SLACK;
    if (!(reach >= 0)) return false;
    return (
      Math.max(l, r) + reach < visible.left ||
      Math.min(l, r) - reach > visible.right ||
      Math.max(t, b) + reach < visible.top ||
      Math.min(t, b) - reach > visible.bottom
    );
  }

  /** The mark's rect, pinned inside the reference rect or the reference rect grown by its outset; false if lengths are unusable. */
  private place(
    plan: MarkPlan,
    l: number,
    t: number,
    r: number,
    b: number,
    circle: number,
  ): boolean {
    const sizes = this.sizes;
    const len = plan.len;
    const dx = sizes.of(plan, Len.Dx);
    const dy = sizes.of(plan, Len.Dy);
    const rect = this.rect;
    if (plan.pin) {
      const ax = plan.pin[0];
      const ay = plan.pin[1];
      const top = sizes.of(plan, Len.MarginTop);
      const right = sizes.of(plan, Len.MarginRight);
      const bottom = sizes.of(plan, Len.MarginBottom);
      const left = sizes.of(plan, Len.MarginLeft);
      const innerW = r - l - left - right;
      const innerH = b - t - top - bottom;
      const w =
        circle > 0 ? 2 * circle : len[Len.W].present ? sizes.of(plan, Len.W) : Math.max(0, innerW);
      const h =
        circle > 0 ? 2 * circle : len[Len.H].present ? sizes.of(plan, Len.H) : Math.max(0, innerH);
      if (
        !(
          top >= 0 &&
          right >= 0 &&
          bottom >= 0 &&
          left >= 0 &&
          w >= 0 &&
          h >= 0 &&
          dx === dx &&
          dy === dy
        )
      )
        return false;
      rect.x = l + left + (innerW - w) * ax + dx;
      rect.y = t + top + (innerH - h) * ay + dy;
      rect.w = w;
      rect.h = h;
      return Number.isFinite(rect.x + rect.y + w + h);
    }
    const outset = sizes.of(plan, Len.Outset);
    if (!(outset === outset && dx === dx && dy === dy)) return false;
    let x = l;
    let y = t;
    let w = r - l;
    let h = b - t;
    if (outset >= 0) {
      x -= outset;
      y -= outset;
      w += 2 * outset;
      h += 2 * outset;
    } else {
      // A negative outset shrinks and stops at the middle, like an inset.
      const mx = Math.min(w / 2, -outset);
      const my = Math.min(h / 2, -outset);
      x += mx;
      y += my;
      w = Math.max(0, w - 2 * mx);
      h = Math.max(0, h - 2 * my);
    }
    rect.x = x + dx;
    rect.y = y + dy;
    rect.w = w;
    rect.h = h;
    return Number.isFinite(rect.x + rect.y + w + h);
  }

  /** Stroke colour and width of the record as the sink takes them: width 0 and colour 0 when there is no stroke. */
  private stroke(plan: MarkPlan, marks: MarkBuffer, base: number): number {
    const flags = marks.words[base + Rec.Flags];
    const width = this.sizes.of(plan, Len.Width);
    return (flags & Flag.Stroke) !== 0 ? width : 0;
  }

  private box(
    plan: MarkPlan,
    marks: MarkBuffer,
    base: number,
    l: number,
    t: number,
    r: number,
    b: number,
    sink: PrimitiveSink,
  ): boolean {
    if (!this.place(plan, l, t, r, b, 0)) return false;
    const width = this.stroke(plan, marks, base);
    const radius = this.sizes.of(plan, Len.Radius);
    if (!(width >= 0 && radius >= 0)) return false;
    const { x, y, w, h } = this.rect;
    if (!(w > 0 && h > 0)) return true;
    const words = marks.words;
    const flags = words[base + Rec.Flags];
    sink.box(
      x,
      y,
      w,
      h,
      width > 0 ? words[base + Rec.Stroke] : 0,
      width,
      width > 0 && (flags & Flag.Dash) !== 0,
      (flags & Flag.Fill) !== 0 ? words[base + Rec.Fill] : 0,
      radius,
    );
    return true;
  }

  private circle(
    plan: MarkPlan,
    marks: MarkBuffer,
    base: number,
    l: number,
    t: number,
    r: number,
    b: number,
    sink: PrimitiveSink,
  ): boolean {
    const radius = this.sizes.of(plan, Len.R);
    const width = this.stroke(plan, marks, base);
    if (!(radius >= 0 && width >= 0) || !this.place(plan, l, t, r, b, radius)) return false;
    if (!(radius > 0)) return true;
    const words = marks.words;
    const flags = words[base + Rec.Flags];
    const { x, y } = this.rect;
    sink.circle(
      x + radius,
      y + radius,
      radius,
      width > 0 ? words[base + Rec.Stroke] : 0,
      width,
      width > 0 && (flags & Flag.Dash) !== 0,
      (flags & Flag.Fill) !== 0 ? words[base + Rec.Fill] : 0,
    );
    return true;
  }

  private bar(
    plan: MarkPlan,
    marks: MarkBuffer,
    base: number,
    l: number,
    t: number,
    r: number,
    b: number,
    sink: PrimitiveSink,
  ): boolean {
    if (!this.place(plan, l, t, r, b, 0)) return false;
    const { x, y, w, h } = this.rect;
    const value = marks.f32[base + Rec.A];
    const filled = h * Math.min(1, Math.max(0, value));
    if (!(r - l > 0 && b - t > 0 && w > 0 && filled > 0)) return true;
    const flags = marks.words[base + Rec.Flags];
    sink.box(
      x,
      y + h - filled,
      w,
      filled,
      0,
      0,
      false,
      (flags & Flag.Fill) !== 0 ? marks.words[base + Rec.Fill] : 0,
      0,
    );
    return true;
  }

  private line(
    plan: MarkPlan,
    marks: MarkBuffer,
    base: number,
    _l: number,
    _t: number,
    _r: number,
    _b: number,
    sink: PrimitiveSink,
  ): boolean {
    const width = this.stroke(plan, marks, base);
    if (!(width >= 0)) return false;
    const flags = marks.words[base + Rec.Flags];
    const f32 = marks.f32;
    const w = this.sizes.imageW;
    const h = this.sizes.imageH;
    sink.line(
      f32[base + Rec.Geometry] * w,
      f32[base + Rec.Geometry + 1] * h,
      f32[base + Rec.Geometry + 2] * w,
      f32[base + Rec.Geometry + 3] * h,
      width > 0 ? marks.words[base + Rec.Stroke] : 0,
      width,
      width > 0 && (flags & Flag.Dash) !== 0,
    );
    return true;
  }

  private polygon(
    plan: MarkPlan,
    marks: MarkBuffer,
    base: number,
    cols: SampleView,
    view: LayoutView,
    sink: PrimitiveSink,
  ): boolean {
    const width = this.stroke(plan, marks, base);
    if (!(width >= 0)) return false;
    const words = marks.words;
    const flags = words[base + Rec.Flags];
    sink.polygon(
      cols.polyPoints,
      words[base + Rec.A],
      words[base + Rec.B],
      view.width,
      view.height,
      width > 0 ? words[base + Rec.Stroke] : 0,
      width,
      width > 0 && (flags & Flag.Dash) !== 0,
      (flags & Flag.Fill) !== 0 ? words[base + Rec.Fill] : 0,
    );
    return true;
  }

  /** The reference's vector arrow: a shaft from the rect's centre and, when the head lengths are set, a filled head. */
  private arrow(
    plan: MarkPlan,
    marks: MarkBuffer,
    base: number,
    l: number,
    t: number,
    r: number,
    b: number,
    view: LayoutView,
    sink: PrimitiveSink,
  ): boolean {
    const sizes = this.sizes;
    const gain = sizes.of(plan, Len.Gain);
    const maximum = sizes.of(plan, Len.Max);
    const headLength = sizes.of(plan, Len.Head);
    const half = sizes.of(plan, Len.HeadWidth);
    const threshold = sizes.of(plan, Len.MinSpeed);
    const width = this.stroke(plan, marks, base);
    if (
      !(gain >= 0 && maximum >= 0 && headLength >= 0 && half >= 0 && threshold >= 0 && width >= 0)
    )
      return false;
    const vx = marks.f32[base + Rec.A];
    const vy = marks.f32[base + Rec.B];
    const dx = vx * view.width * gain;
    const dy = vy * view.height * gain;
    const magnitude = Math.hypot(dx, dy);
    if (Math.hypot(vx, vy) <= threshold || gain === 0 || maximum === 0) return true;
    if (!Number.isFinite(magnitude)) return false;
    if (magnitude === 0) return true;
    const ux = dx / magnitude;
    const uy = dy / magnitude;
    const shaft = Math.min(magnitude, maximum);
    const ox = (l + r) / 2;
    const oy = (t + b) / 2;
    const ex = ox + ux * shaft;
    const ey = oy + uy * shaft;
    const stroke = marks.words[base + Rec.Stroke];
    sink.line(ox, oy, ex, ey, width > 0 ? stroke : 0, width, false);
    if (!(headLength > 0 && half > 0)) return true;
    const fraction = Math.min(1, shaft / headLength);
    const length = headLength * fraction;
    const spread = half * fraction;
    const head = this.head;
    head[0] = ex / view.width;
    head[1] = ey / view.height;
    head[2] = (ex - ux * length - uy * spread) / view.width;
    head[3] = (ey - uy * length + ux * spread) / view.height;
    head[4] = (ex - ux * length + uy * spread) / view.width;
    head[5] = (ey - uy * length - ux * spread) / view.height;
    sink.polygon(head, 0, 3, view.width, view.height, 0, 0, false, (stroke | 0xff) >>> 0);
    return true;
  }
}
