/**
 * The overlay quad: one instanced quad per shape, glyph or label part, in target CSS pixels, and the writer that
 * collects them. The fragment shader (painter shaders) turns each into antialiased ink by its kind.
 *
 * A quad's twelve 4-byte fields: x, y (top-left before padding), ux, uy (unit u axis; a line's direction), w, h (shape
 * size), radius (corner or cap radius; a glyph's atlas u texel), stroke (inward stroke width; a line's dash phase; a
 * glyph's atlas v texel), dash (dash period, 0 solid), kind, fill and pen (premultiplied RGBA bytes). Quads hold the
 * shape itself; the vertex shader pads shape quads by `PAD` on every side for the edge antialiasing, glyphs not.
 */

/** Floats per quad. */
export const QUAD_FLOATS = 12;
/** Shape quads extend this many target pixels beyond the shape so the shader can antialias its edge. */
export const PAD = 1;
/** The dash period in stroke widths: a dash and a gap of three widths each. */
export const DASH_PERIOD = 6;

/** What a quad draws, read by the fragment shader. */
export const Kind = { rect: 0, ellipse: 1, line: 2, glyph: 3 } as const;
/** One of `Kind`. */
export type Kind = (typeof Kind)[keyof typeof Kind];

/** Premultiplied RGBA packed into one little-endian word: red in the low byte. */
export function premultiplied(red: number, green: number, blue: number, alpha: number): number {
  const scale = alpha / 255;
  return (
    (Math.round(red * scale) |
      (Math.round(green * scale) << 8) |
      (Math.round(blue * scale) << 16) |
      (alpha << 24)) >>>
    0
  );
}

/**
 * A growable quad list reused frame after frame, so preparing a drawing allocates only the arrays it hands out.
 */
export class QuadWriter {
  #f32 = new Float32Array(QUAD_FLOATS * 256);
  #u32 = new Uint32Array(this.#f32.buffer);
  #length = 0;

  /** Quads written since the last `clear`. */
  get count(): number {
    return this.#length / QUAD_FLOATS;
  }

  clear(): void {
    this.#length = 0;
  }

  /** Drops the quads written after the first `count`. */
  truncate(count: number): void {
    this.#length = Math.min(this.#length, count * QUAD_FLOATS);
  }

  push(
    kind: Kind,
    x: number,
    y: number,
    ux: number,
    uy: number,
    w: number,
    h: number,
    radius: number,
    stroke: number,
    dash: number,
    fill: number,
    pen: number,
  ): void {
    if (this.#length + QUAD_FLOATS > this.#f32.length) {
      const grown = new Float32Array(this.#f32.length * 2);
      grown.set(this.#f32);
      this.#f32 = grown;
      this.#u32 = new Uint32Array(grown.buffer);
    }
    const f = this.#f32;
    const at = this.#length;
    f[at] = x;
    f[at + 1] = y;
    f[at + 2] = ux;
    f[at + 3] = uy;
    f[at + 4] = w;
    f[at + 5] = h;
    f[at + 6] = radius;
    f[at + 7] = stroke;
    f[at + 8] = dash;
    f[at + 9] = kind;
    this.#u32[at + 10] = fill;
    this.#u32[at + 11] = pen;
    this.#length = at + QUAD_FLOATS;
  }

  /** A filled rounded rectangle without stroke, padded for antialiasing (label backgrounds and bars). */
  rect(
    left: number,
    top: number,
    width: number,
    height: number,
    radius: number,
    fill: number,
  ): void {
    this.push(Kind.rect, left, top, 1, 0, width, height, radius, 0, 0, fill, 0);
  }

  /** The quads written since `from`, copied into an array of their own that can be transferred. */
  take(from = 0): Float32Array {
    return this.#f32.slice(from * QUAD_FLOATS, this.#length);
  }

  /** The quads from `from` up to `to` (default: all written), viewed in place: valid until the next write or clear. */
  subarray(from = 0, to = this.count): Float32Array {
    return this.#f32.subarray(from * QUAD_FLOATS, to * QUAD_FLOATS);
  }
}

/** One shape's paint, resolved to target pixels and packed colors. */
export type ShapePaint = {
  stroke: number;
  dash: number;
  fill: number;
  pen: number;
  radius: number;
};

/** Rectangles (`Kind.rect`) and ellipses (`Kind.ellipse`) outlined around the box `left, top, width, height`. */
export function outlined(
  out: QuadWriter,
  kind: Kind,
  left: number,
  top: number,
  width: number,
  height: number,
  paint: ShapePaint,
): void {
  const stroke = paint.stroke;
  if (!(stroke >= 0 && stroke < Infinity)) return;
  if (stroke === 0 && paint.fill >>> 24 === 0) return;
  const half = stroke / 2;
  // The stroke is centred on the outline while the shader strokes inward, so the quad grows by half a stroke; the
  // corner radius is clamped on the centre line, the way the reference's painter did.
  const radius = paint.radius > 0 ? Math.min(paint.radius, Math.min(width, height) / 2) + half : 0;
  out.push(
    kind,
    left - half,
    top - half,
    1,
    0,
    width + stroke,
    height + stroke,
    radius,
    stroke,
    paint.dash,
    paint.fill,
    paint.pen,
  );
}

/** A solid square of the pen color centred on a point. */
export function square(out: QuadWriter, cx: number, cy: number, size: number, color: number): void {
  if (!(size > 0 && size < Infinity && Number.isFinite(cx) && Number.isFinite(cy))) return;
  out.rect(cx - size / 2, cy - size / 2, size, size, 0, color);
}

/**
 * A stroke from (x1, y1) to (x2, y2) as a rotated rectangle. `cap` is the cap radius (0 square, half the stroke
 * round) and `phase` how far along the dash pattern the line starts. Returns the segment's length.
 */
export function segment(
  out: QuadWriter,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  stroke: number,
  cap: number,
  phase: number,
  dash: number,
  color: number,
): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const length = Math.hypot(dx, dy);
  if (!Number.isFinite(length) || !Number.isFinite(x1) || !Number.isFinite(y1)) return 0;
  if (length === 0 || !(stroke > 0 && stroke < Infinity)) return length;
  const ux = dx / length;
  const uy = dy / length;
  const half = stroke / 2;
  // The quad's u axis runs along the line and its v axis across; its origin is half a stroke behind the start and
  // half a stroke to the left.
  out.push(
    Kind.line,
    x1 - half * (ux - uy),
    y1 - half * (uy + ux),
    ux,
    uy,
    length + stroke,
    stroke,
    cap,
    phase - half,
    dash,
    color,
    color,
  );
  return length;
}

/**
 * The strokes of a path of `count` points from `first` in `points` (x, y pairs, normalized), scaled to the target:
 * round joins and ends, one dash pattern running along the whole path. `closed` adds the edge back to the start.
 */
export function path(
  out: QuadWriter,
  points: Float32Array,
  first: number,
  count: number,
  closed: boolean,
  width: number,
  height: number,
  stroke: number,
  dash: number,
  color: number,
): void {
  if (!(stroke > 0 && stroke < Infinity)) return;
  let travelled = 0;
  const edges = closed ? count : count - 1;
  for (let edge = 0; edge < edges; edge++) {
    const from = (first + edge) * 2;
    const to = (first + ((edge + 1) % count)) * 2;
    travelled += segment(
      out,
      points[from] * width,
      points[from + 1] * height,
      points[to] * width,
      points[to + 1] * height,
      stroke,
      stroke / 2,
      travelled,
      dash,
      color,
    );
  }
}
