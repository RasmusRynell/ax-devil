import type { MarkBuffer } from "../marks/buffer";
import { Flag, RUN_WORDS, Rec } from "../marks/buffer";
import { Kind, Len, type MarkPlan } from "../marks/plan";
import { GlyphScratch } from "../text/measure";
import { LabelBox } from "../text/labelBox";
import type { FontHandle, TextSource } from "../text/source";
import type { Resolver } from "./resolver";
import type { LayoutView, PrimitiveSink } from "./sink";

/** A bar's length and thickness in font sizes, and the opacity of its unfilled track. */
const BAR_LENGTH = 2.5;
const BAR_THICKNESS = 0.3;
const BAR_TRACK_ALPHA = 70;
/** Bars are never thinner than this many target pixels. */
const BAR_MIN = 2;
/** How far past the image edge a label may reach and still count as inside it. */
const EDGE = 1e-4;

/** A font size in device pixels, snapped to halves so sizes that follow object sizes share a few fonts. */
export function devicePixels(size: number, dpr: number): number {
  return Math.max(0.5, Math.round(size * dpr * 2) / 2);
}

/** Lays out the texts and labels of a lane: measures their runs, picks a placement and writes background, bars and glyphs. */
export class Labels {
  private readonly scratch = new GlyphScratch();
  private readonly box = new LabelBox();
  private fontPx = new Float64Array(0);
  private fonts: (FontHandle | undefined)[] = [];
  /** Per run of the label being laid out: its font and the glyph range it measured. */
  private runFont: FontHandle[] = [];
  private runFirst = new Uint32Array(16);
  private runEnd = new Uint32Array(16);

  constructor(
    private readonly text: TextSource,
    private readonly sizes: Resolver,
  ) {}

  /** Forget the fonts of a catalog before another one is laid out. */
  reset(marks: number): void {
    this.fontPx = new Float64Array(marks * 4);
    this.fonts = Array.from({ length: marks * 4 }, (): FontHandle | undefined => undefined);
  }

  private fontFor(plan: MarkPlan, weight: number, pixels: number): FontHandle {
    const slot = plan.index * 4 + weight;
    let font = this.fonts[slot];
    if (font === undefined || this.fontPx[slot] !== pixels) {
      font = this.text.font(plan.family, pixels, weight);
      this.fonts[slot] = font;
      this.fontPx[slot] = pixels;
    }
    return font;
  }

  /**
   * Lay out the text or label of the record at `base`, its reference rect (`l`, `t`, `r`, `b`) in target pixels.
   * False when its lengths do not work. A label with nothing to show, or no candidate placement, draws nothing.
   */
  draw(
    plan: MarkPlan,
    marks: MarkBuffer,
    record: number,
    l: number,
    t: number,
    r: number,
    b: number,
    view: LayoutView,
    sink: PrimitiveSink,
  ): boolean {
    const { sizes, box, scratch } = this;
    const words = marks.words;
    const base = record * 12;
    const plain = plan.kind === Kind.Text;
    const size = sizes.length(plan.len[Len.Size]);
    const padX = sizes.length(plan.len[Len.PadX]);
    const padY = sizes.length(plan.len[Len.PadY]);
    const gap = sizes.length(plan.len[Len.Gap]);
    const radius = sizes.length(plan.len[Len.Radius]);
    if (!(size > 0 && size < Infinity && padX >= 0 && padY >= 0 && gap >= 0 && radius >= 0))
      return false;
    const { dpr } = view;
    const pixels = devicePixels(size, dpr);
    const first = words[base + Rec.A];
    const count = words[base + Rec.B];
    box.clear();
    scratch.clear();
    if (this.runFirst.length < count) {
      this.runFirst = new Uint32Array(count * 2);
      this.runEnd = new Uint32Array(count * 2);
    }
    for (let k = 0; k < count; k++) {
      const at = (first + k) * RUN_WORDS;
      const font = this.fontFor(plan, marks.runs[at + 3], pixels);
      this.runFont[k] = font;
      const { ascent, descent, capHeight } = font.metrics;
      this.runFirst[k] = scratch.count;
      if (Number.isNaN(marks.runBars[first + k])) {
        scratch.measureRun(
          this.text,
          font,
          marks.cps,
          marks.runs[at],
          marks.runs[at] + marks.runs[at + 1],
        );
        box.add(
          true,
          scratch.advance / dpr,
          scratch.left / dpr,
          scratch.top / dpr,
          scratch.right / dpr,
          scratch.bottom / dpr,
          plain ? 0 : ascent / dpr,
          plain ? 0 : descent / dpr,
        );
      } else {
        const middle = -capHeight / dpr / 2;
        const half = Math.max(BAR_MIN, size * BAR_THICKNESS) / 2;
        box.add(
          true,
          size * BAR_LENGTH,
          0,
          middle - half,
          size * BAR_LENGTH,
          middle + half,
          ascent / dpr,
          descent / dpr,
        );
      }
      this.runEnd[k] = scratch.count;
    }
    if (!box.size(padX, padY, gap, dpr, plain)) return true;
    if (!this.choose(plan, marks, base, l, t, r, b, view)) return true;
    const placed = plan.place[this.chosen];
    if (plain) sink.text(record, this.x, this.y, placed.anchorX, placed.anchorY, size);
    else
      sink.label(
        record,
        this.x,
        this.y,
        placed.anchorX,
        placed.anchorY,
        size,
        padX,
        padY,
        radius,
        gap,
      );
    this.emit(marks, base, first, count, size, radius, dpr, plain, sink);
    return true;
  }

  /** The placement point of the chosen candidate. */
  private x = 0;
  private y = 0;
  private chosen = 0;

  /**
   * The first candidate whose box stays inside the image on every side where it puts the box outside the reference
   * rect, else the last candidate. Leaves the box placed there. False when no candidate applies.
   */
  private choose(
    plan: MarkPlan,
    marks: MarkBuffer,
    base: number,
    l: number,
    t: number,
    r: number,
    b: number,
    view: LayoutView,
  ): boolean {
    const mask = (marks.words[base + Rec.Flags] >>> 8) & 0xff;
    let last = -1;
    for (let i = 0; i < plan.place.length; i++) {
      if ((mask & (1 << i)) === 0) continue;
      last = i;
      if (this.tryPlace(plan, i, l, t, r, b, view) && this.fits(plan, i, view)) return true;
    }
    if (last < 0) return false;
    return this.tryPlace(plan, last, l, t, r, b, view);
  }

  private tryPlace(
    plan: MarkPlan,
    i: number,
    l: number,
    t: number,
    r: number,
    b: number,
    view: LayoutView,
  ): boolean {
    const entry = plan.place[i];
    const dx = this.sizes.length(entry.dx);
    const dy = this.sizes.length(entry.dy);
    this.x = l + entry.atX * (r - l) + dx;
    this.y = t + entry.atY * (b - t) + dy;
    this.chosen = i;
    return this.box.place(this.x, this.y, entry.anchorX, entry.anchorY, view.dpr);
  }

  /** Whether candidate `i`, already placed, stays inside the image on the axes where it lies outside its reference rect. */
  private fits(plan: MarkPlan, i: number, view: LayoutView): boolean {
    const entry = plan.place[i];
    const { box } = this;
    if (entry.atX === 0 && entry.anchorX === 1 && box.x < -EDGE) return false;
    if (entry.atX === 1 && entry.anchorX === 0 && box.x + box.width > view.width + EDGE)
      return false;
    if (entry.atY === 0 && entry.anchorY === 1 && box.y < -EDGE) return false;
    if (entry.atY === 1 && entry.anchorY === 0 && box.y + box.height > view.height + EDGE)
      return false;
    return true;
  }

  private emit(
    marks: MarkBuffer,
    base: number,
    first: number,
    count: number,
    size: number,
    radius: number,
    dpr: number,
    plain: boolean,
    sink: PrimitiveSink,
  ): void {
    const { box, scratch } = this;
    const flags = marks.words[base + Rec.Flags];
    const background = marks.words[base + Rec.Fill];
    if (!plain && (flags & Flag.Fill) !== 0 && (background & 0xff) > 0)
      sink.rect(
        box.x,
        box.y,
        box.width,
        box.height,
        Math.min(radius, Math.min(box.width, box.height) / 2),
        background,
      );
    const thickness = Math.max(BAR_MIN, size * BAR_THICKNESS);
    for (let k = 0; k < count; k++) {
      const bar = marks.runBars[first + k];
      if (Number.isNaN(bar)) continue;
      const color = marks.runs[(first + k) * RUN_WORDS + 2];
      const left = box.innerX + box.offset(k);
      const top = box.baseline - this.runFont[k].metrics.capHeight / dpr / 2 - thickness / 2;
      const length = size * BAR_LENGTH;
      const track = (color & 0xffff_ff00) | Math.round((BAR_TRACK_ALPHA * (color & 0xff)) / 255);
      sink.rect(left, top, length, thickness, thickness / 2, track >>> 0);
      if (bar > 0)
        sink.rect(left, top, Math.max(thickness, length * bar), thickness, thickness / 2, color);
    }
    for (let k = 0; k < count; k++) {
      if (!Number.isNaN(marks.runBars[first + k])) continue;
      const color = marks.runs[(first + k) * RUN_WORDS + 2];
      const originX = Math.round((box.innerX + box.offset(k)) * dpr);
      const originY = Math.round(box.baseline * dpr);
      sink.glyphs(scratch, this.runFirst[k], this.runEnd[k], originX, originY, dpr, color);
    }
  }
}
