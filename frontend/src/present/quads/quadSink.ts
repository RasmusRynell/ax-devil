import {
  DASH_PERIOD,
  Kind,
  type ShapePaint,
  outlined,
  path,
  premultiplied,
  segment,
  square,
} from "../../overlay/quads";
import type { PrimitiveSink } from "../layout/sink";
import { GLYPH, type RunCache } from "../text/runCache";
import type { LaneQuads } from "./lane";

/** The colours seen lately and their words: a frame uses a handful, over and over. */
const seen = new Uint32Array(64);
const words = new Uint32Array(64);

/** A packed `0xRRGGBBAA` colour as the premultiplied word the quads carry. */
export function premultipliedWord(rgba: number): number {
  const slot = (rgba ^ (rgba >>> 11)) & 63;
  if (seen[slot] === rgba) return words[slot];
  const word = premultiplied(rgba >>> 24, (rgba >>> 16) & 0xff, (rgba >>> 8) & 0xff, rgba & 0xff);
  seen[slot] = rgba;
  words[slot] = word;
  return word;
}

/** Writes layout's primitives as the instanced quads the painter draws. */
export class QuadSink implements PrimitiveSink {
  private readonly paint: ShapePaint = { stroke: 0, dash: 0, fill: 0, pen: 0, radius: 0 };

  constructor(private readonly out: LaneQuads) {}

  begin(): void {
    this.out.clear();
  }

  endLayer(index: number): void {
    this.out.endLayer(index);
  }

  private use(
    stroke: number,
    width: number,
    dashed: boolean,
    fill: number,
    radius: number,
  ): ShapePaint {
    const paint = this.paint;
    paint.stroke = width;
    paint.dash = dashed ? width * DASH_PERIOD : 0;
    paint.pen = premultipliedWord(stroke);
    paint.fill = premultipliedWord(fill);
    paint.radius = radius;
    return paint;
  }

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
    if (!(width > 0 && height > 0 && Number.isFinite(left + top + width + height))) return;
    outlined(
      this.out.quads,
      Kind.rect,
      left,
      top,
      width,
      height,
      this.use(stroke, strokeWidth, dashed, fill, radius),
    );
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
    if (!(radius > 0 && Number.isFinite(cx + cy + radius))) return;
    outlined(
      this.out.quads,
      Kind.ellipse,
      cx - radius,
      cy - radius,
      2 * radius,
      2 * radius,
      this.use(stroke, strokeWidth, dashed, fill, 0),
    );
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
    if (!Number.isFinite(x1 + y1 + x2 + y2) || !(strokeWidth > 0)) return;
    const pen = premultipliedWord(stroke);
    // Lines have square caps; coincident ends draw a point as wide as the stroke.
    if (x1 === x2 && y1 === y2) square(this.out.quads, x1, y1, strokeWidth, pen);
    else
      segment(
        this.out.quads,
        x1,
        y1,
        x2,
        y2,
        strokeWidth,
        0,
        0,
        dashed ? strokeWidth * DASH_PERIOD : 0,
        pen,
      );
  }

  polygon(
    points: Float32Array,
    first: number,
    count: number,
    scaleX: number,
    scaleY: number,
    stroke: number,
    strokeWidth: number,
    dashed: boolean,
    fill: number,
  ): void {
    if (count < 3) return;
    if ((fill & 0xff) > 0)
      this.out.fills.add(points, first, count, scaleX, scaleY, premultipliedWord(fill));
    if (strokeWidth > 0)
      path(
        this.out.quads,
        points,
        first,
        count,
        true,
        scaleX,
        scaleY,
        strokeWidth,
        dashed ? strokeWidth * DASH_PERIOD : 0,
        premultipliedWord(stroke),
      );
  }

  // The text and label announcements are for the canonical sink; their parts arrive as `rect` and `glyphs`.
  text(): void {}

  label(): void {}

  rect(
    left: number,
    top: number,
    width: number,
    height: number,
    radius: number,
    fill: number,
  ): void {
    this.out.quads.rect(left, top, width, height, radius, premultipliedWord(fill));
  }

  glyphs(
    runs: RunCache,
    entry: number,
    originX: number,
    originY: number,
    dpr: number,
    color: number,
  ): void {
    const word = premultipliedWord(color);
    const quads = this.out.quads;
    const inverse = 1 / dpr;
    const pool = runs.glyphs;
    const end = runs.glyphEnd[entry] * GLYPH;
    for (let at = runs.glyphFirst[entry] * GLYPH; at < end; at += GLYPH) {
      quads.push(
        Kind.glyph,
        (originX + pool[at]) * inverse,
        (originY + pool[at + 1]) * inverse,
        1,
        0,
        pool[at + 2] * inverse,
        pool[at + 3] * inverse,
        pool[at + 4],
        pool[at + 5],
        0,
        word,
        0,
      );
    }
  }
}
