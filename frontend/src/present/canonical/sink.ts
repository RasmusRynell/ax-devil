import { Flag, type MarkBuffer, RUN_WORDS, Rec } from "../marks/buffer";
import { BASELINE_ANCHOR, WEIGHTS } from "../catalog/types";
import type { PrimitiveSink } from "../layout/sink";
import type { PlacePlan } from "../marks/plan";
import { codePointString } from "../expr/codePoints";

/** Oracle rows (C5 of `fixtures/oracle/README.md`): plain JSON values keyed as the reference's files are. */
export type Row = Record<string, unknown>;
/** The eight kinds of rows, each a list. */
export type Rows = Record<
  "boxes" | "circles" | "lines" | "points" | "polygons" | "polylines" | "texts" | "labels",
  Row[]
>;

const ANCHORS = [
  ["top-left", "top-center", "top-right"],
  ["center-left", "center", "center-right"],
  ["bottom-left", "bottom-center", "bottom-right"],
] as const;

function anchorName(place: PlacePlan): string {
  return place.baseline ? BASELINE_ANCHOR : ANCHORS[place.anchorY * 2][place.anchorX * 2];
}

/** What the sink needs to read a label's runs and the font family of its mark. */
export interface RowSource {
  readonly marks: MarkBuffer;
  /** The font family of the mark at a record. */
  family(record: number): string;
}

/**
 * Test-only sink that records the primitives as oracle rows: coordinates normalized by the image size, lengths by its
 * shorter side, floats rounded to five decimals. Absent strokes and fills are zeros, as in the reference's files.
 */
export class CanonicalSink implements PrimitiveSink {
  rows: Rows = emptyRows();
  private readonly scale: number;

  constructor(
    private readonly source: RowSource,
    private readonly width: number,
    private readonly height: number,
  ) {
    this.scale = Math.min(width, height);
  }

  begin(): void {
    this.rows = emptyRows();
  }

  endLayer(): void {}

  private x = (v: number): number => round(v / this.width);
  private y = (v: number): number => round(v / this.height);
  private s = (v: number): number => round(v / this.scale);

  box(
    left: number,
    top: number,
    width: number,
    height: number,
    stroke: number,
    strokeWidth: number,
    dashed: boolean,
    fill: number,
    radius: number,
  ): void {
    this.rows.boxes.push({
      x: this.x(left),
      y: this.y(top),
      w: this.x(width),
      h: this.y(height),
      ...this.paint(stroke, strokeWidth, dashed),
      brush: channels4(fill),
      radius: this.s(radius),
    });
  }

  circle(
    cx: number,
    cy: number,
    radius: number,
    stroke: number,
    strokeWidth: number,
    dashed: boolean,
    fill: number,
  ): void {
    this.rows.circles.push({
      x: this.x(cx),
      y: this.y(cy),
      r: this.s(radius),
      ...this.paint(stroke, strokeWidth, dashed),
      brush: channels4(fill),
    });
  }

  line(
    x1: number,
    y1: number,
    x2: number,
    y2: number,
    stroke: number,
    strokeWidth: number,
    dashed: boolean,
  ): void {
    this.rows.lines.push({
      x1: this.x(x1),
      y1: this.y(y1),
      x2: this.x(x2),
      y2: this.y(y2),
      ...this.paint(stroke, strokeWidth, dashed),
    });
  }

  polygon(
    points: Float32Array,
    first: number,
    count: number,
    _sx: number,
    _sy: number,
    stroke: number,
    strokeWidth: number,
    dashed: boolean,
    fill: number,
  ): void {
    const list: number[][] = [];
    for (let i = 0; i < count; i++)
      list.push([round(points[(first + i) * 2]), round(points[(first + i) * 2 + 1])]);
    this.rows.polygons.push({
      points: list,
      ...this.paint(stroke, strokeWidth, dashed),
      brush: channels4(fill),
    });
  }

  text(record: number, x: number, y: number, place: PlacePlan, size: number): void {
    const { marks } = this.source;
    const run = marks.words[record * 12 + Rec.A] * RUN_WORDS;
    this.rows.texts.push({
      x: this.x(x),
      y: this.y(y),
      text: this.string(marks.runs[run], marks.runs[run + 1]),
      anchor: anchorName(place),
      color: channels3(marks.runs[run + 2]),
      font_family: this.source.family(record),
      font_size: this.s(size),
    });
  }

  label(
    record: number,
    x: number,
    y: number,
    place: PlacePlan,
    size: number,
    padX: number,
    padY: number,
    radius: number,
    gap: number,
  ): void {
    const { marks } = this.source;
    const first = marks.words[record * 12 + Rec.A];
    const count = marks.words[record * 12 + Rec.B];
    const runs: Row[] = [];
    for (let k = first; k < first + count; k++) {
      const at = k * RUN_WORDS;
      const bar = marks.runBars[k];
      runs.push({
        text: Number.isNaN(bar) ? this.string(marks.runs[at], marks.runs[at + 1]) : "",
        bar: Number.isNaN(bar) ? null : round(bar),
        rgb: channels3(marks.runs[at + 2]),
        weight: WEIGHTS[marks.runs[at + 3]],
      });
    }
    const background = marks.words[record * 12 + Rec.Fill];
    const filled =
      (marks.words[record * 12 + Rec.Flags] & Flag.Fill) !== 0 && (background & 0xff) > 0;
    this.rows.labels.push({
      x: this.x(x),
      y: this.y(y),
      anchor: anchorName(place),
      size: this.s(size),
      family: this.source.family(record),
      background: filled ? channels4(background) : null,
      padding_x: this.s(padX),
      padding_y: this.s(padY),
      radius: this.s(radius),
      gap: this.s(gap),
      runs,
    });
  }

  rect(): void {}

  glyphs(): void {}

  private paint(stroke: number, width: number, dashed: boolean): Row {
    return { pen: channels3(stroke), pen_width: this.s(width), dashed };
  }

  private string(start: number, length: number): string {
    return codePointString(this.source.marks.cps.data, start, start + length);
  }
}

function emptyRows(): Rows {
  return {
    boxes: [],
    circles: [],
    lines: [],
    points: [],
    polygons: [],
    polylines: [],
    texts: [],
    labels: [],
  };
}

/** Five decimals, and never `-0`. */
function round(value: number): number {
  const rounded = Math.round(value * 1e5) / 1e5;
  return rounded === 0 ? 0 : rounded;
}

function channels3(rgba: number): number[] {
  return [rgba >>> 24, (rgba >>> 16) & 0xff, (rgba >>> 8) & 0xff];
}

function channels4(rgba: number): number[] {
  return [rgba >>> 24, (rgba >>> 16) & 0xff, (rgba >>> 8) & 0xff, rgba & 0xff];
}
