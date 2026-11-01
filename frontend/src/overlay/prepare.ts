/**
 * Preparation of a lane frame's Drawing Instructions into what the painter draws, in the playback worker: shape
 * quads, polygon fills, and text and label quads on the worker's text atlas, in target CSS pixels. The arrays are
 * fresh per drawing so they can be transferred; the writers behind them are reused.
 */

import { AtlasFull, type AtlasUpload, type TextAtlas } from "./atlas";
import type { DecodedInstructions, Table } from "./instructions";
import { drawLabels, drawTexts } from "./labels";
import {
  DASH_PERIOD,
  Kind,
  QuadWriter,
  outlined,
  path,
  premultiplied,
  segment,
  square,
  type ShapePaint,
} from "./quads";

/** A drawing ready for the painter. Quads are `QUAD_FLOATS` floats each, in target CSS pixels. */
export type PreparedDrawing = {
  /** The atlas generation the text quads sample; a drawing of another generation draws without them. */
  generation: number;
  /** Boxes, circles, points, lines, polyline and polygon outlines. */
  shapes: Float32Array;
  /** Texts, then labels (backgrounds, bars and glyphs). */
  texts: Float32Array;
  /**
   * Polygon fills, one block each: corner count, fill (RGBA word), left, top, right, bottom, then the corners as
   * x, y pairs. Filled odd-even through the stencil.
   */
  polygons: Float32Array;
  /** Atlas changes to apply, in order, before this or any later drawing is drawn. */
  uploads: AtlasUpload[];
  /** Applied to the whole drawing; sticky overlays draw at their retained opacity. */
  opacity: number;
  /** The CSS pixel size the drawing was prepared for; the painter stretches it onto the picture it covers. */
  target: { width: number; height: number };
  /** The device pixel ratio the glyphs were rasterized at. */
  dpr: number;
};

const shapes = new QuadWriter();
const texts = new QuadWriter();
const paint: ShapePaint = { stroke: 0, dash: 0, fill: 0, pen: 0, radius: 0 };
let polygons = new Float32Array(1024);
let polygonWords = new Uint32Array(polygons.buffer);
let polygonLength = 0;

/** Resolves paint `index` into `paint` for the target; false when the row points past the paints. */
function usePaint(d: DecodedInstructions, index: number, scale: number): boolean {
  if (index >= d.paints.count) return false;
  const { f32, u8 } = d.paints;
  const at = index * 6;
  const b = at * 4;
  const stroke = Math.max(0, f32[at + 1] * scale);
  paint.stroke = stroke;
  paint.dash = u8[b + 3] !== 0 ? stroke * DASH_PERIOD : 0;
  paint.pen = premultiplied(u8[b], u8[b + 1], u8[b + 2], 255);
  paint.fill = premultiplied(u8[b + 8], u8[b + 9], u8[b + 10], u8[b + 11]);
  paint.radius = f32[at + 5] * scale || 0;
  return true;
}

function finite(a: number, b: number, c = 0, d = 0): boolean {
  return Number.isFinite(a) && Number.isFinite(b) && Number.isFinite(c) && Number.isFinite(d);
}

function drawShapes(d: DecodedInstructions, width: number, height: number, scale: number): void {
  const { boxes, circles, points, lines } = d;
  for (let row = 0; row < boxes.count; row++) {
    const at = row * 5;
    if (!usePaint(d, boxes.u32[at + 4], scale)) continue;
    const f = boxes.f32;
    const left = f[at] * width;
    const top = f[at + 1] * height;
    const w = f[at + 2] * width;
    const h = f[at + 3] * height;
    if (w > 0 && h > 0 && finite(left, top, w, h))
      outlined(shapes, Kind.rect, left, top, w, h, paint);
  }
  for (let row = 0; row < circles.count; row++) {
    const at = row * 4;
    if (!usePaint(d, circles.u32[at + 3], scale)) continue;
    const f = circles.f32;
    const r = f[at + 2] * scale;
    const x = f[at] * width;
    const y = f[at + 1] * height;
    paint.radius = 0;
    if (r > 0 && finite(r, x, y)) outlined(shapes, Kind.ellipse, x - r, y - r, 2 * r, 2 * r, paint);
  }
  for (let row = 0; row < points.count; row++) {
    const at = row * 3;
    if (!usePaint(d, points.u32[at + 2], scale)) continue;
    square(shapes, points.f32[at] * width, points.f32[at + 1] * height, paint.stroke, paint.pen);
  }
  for (let row = 0; row < lines.count; row++) {
    const at = row * 5;
    if (!usePaint(d, lines.u32[at + 4], scale)) continue;
    const f = lines.f32;
    const x1 = f[at] * width;
    const y1 = f[at + 1] * height;
    const x2 = f[at + 2] * width;
    const y2 = f[at + 3] * height;
    if (!finite(x1, y1, x2, y2)) continue;
    // Lines have square caps; coincident ends draw a point as wide as the stroke.
    if (x1 === x2 && y1 === y2) square(shapes, x1, y1, paint.stroke, paint.pen);
    else segment(shapes, x1, y1, x2, y2, paint.stroke, 0, 0, paint.dash, paint.pen);
  }
  drawPaths(d, d.polylines, false, width, height, scale);
  drawPaths(d, d.polygons, true, width, height, scale);
}

function drawPaths(
  d: DecodedInstructions,
  table: Table,
  closed: boolean,
  width: number,
  height: number,
  scale: number,
): void {
  const points = d.pathPoints;
  for (let row = 0; row < table.count; row++) {
    const first = table.u32[row * 3];
    const count = table.u32[row * 3 + 1];
    if (count < (closed ? 3 : 2) || first + count > points.count) continue;
    if (!usePaint(d, table.u32[row * 3 + 2], scale)) continue;
    if (closed && paint.fill >>> 24 > 0) fill(points.f32, first, count, width, height, paint.fill);
    path(
      shapes,
      points.f32,
      first,
      count,
      closed,
      width,
      height,
      paint.stroke,
      paint.dash,
      paint.pen,
    );
  }
}

/** Adds a polygon fill block; polygons with a non-finite corner or no extent are not filled. */
function fill(
  points: Float32Array,
  first: number,
  count: number,
  width: number,
  height: number,
  color: number,
) {
  const needed = polygonLength + 6 + count * 2;
  if (needed > polygons.length) {
    const grown = new Float32Array(Math.max(needed, polygons.length * 2));
    grown.set(polygons.subarray(0, polygonLength));
    polygons = grown;
    polygonWords = new Uint32Array(grown.buffer);
  }
  const at = polygonLength;
  let left = Infinity;
  let top = Infinity;
  let right = -Infinity;
  let bottom = -Infinity;
  for (let corner = 0; corner < count; corner++) {
    const x = points[(first + corner) * 2] * width;
    const y = points[(first + corner) * 2 + 1] * height;
    if (!finite(x, y)) return;
    polygons[at + 6 + corner * 2] = x;
    polygons[at + 7 + corner * 2] = y;
    left = Math.min(left, x);
    top = Math.min(top, y);
    right = Math.max(right, x);
    bottom = Math.max(bottom, y);
  }
  if (!(right > left && bottom > top)) return;
  polygons[at] = count;
  polygonWords[at + 1] = color;
  polygons[at + 2] = left;
  polygons[at + 3] = top;
  polygons[at + 4] = right;
  polygons[at + 5] = bottom;
  polygonLength = needed;
}

/**
 * Prepares one lane frame's Drawing Instructions for a target of `target` CSS pixels at device pixel ratio `dpr`.
 * Text goes onto `atlas`; when its page fills up the atlas starts a new generation and this drawing is laid out
 * again on it, so drawings prepared earlier must be prepared again (`OverlayPreparer` does that).
 */
export function prepareDrawing(
  instructions: DecodedInstructions,
  target: { width: number; height: number },
  dpr: number,
  atlas: TextAtlas,
  opacity = 1,
): PreparedDrawing {
  const { width, height } = target;
  const scale = Math.min(width, height);
  shapes.clear();
  texts.clear();
  polygonLength = 0;
  if (scale > 0 && Number.isFinite(scale) && dpr > 0) {
    drawShapes(instructions, width, height, scale);
    const text = { width, height, scale, dpr, atlas };
    const dropping = atlas.dropOverflow;
    try {
      drawTexts(instructions, text, texts);
      drawLabels(instructions, text, texts);
    } catch (error) {
      if (!(error instanceof AtlasFull)) throw error;
      // A fresh page, and the glyphs that still do not fit are left out rather than failing the frame.
      texts.clear();
      atlas.reset();
      atlas.dropOverflow = true;
      try {
        drawTexts(instructions, text, texts);
        drawLabels(instructions, text, texts);
      } finally {
        atlas.dropOverflow = dropping;
      }
    }
  }
  return {
    generation: atlas.generation,
    shapes: shapes.take(),
    texts: texts.take(),
    polygons: polygons.slice(0, polygonLength),
    uploads: atlas.takeUploads(),
    opacity,
    target: { width, height },
    dpr,
  };
}
