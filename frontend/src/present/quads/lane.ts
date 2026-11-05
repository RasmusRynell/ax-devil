import { FILL_HEADER_FLOATS, FILL_VERTEX_FLOATS, QuadWriter } from "../../overlay/quads";
import { CORNER_FLOATS, cornerOffsets } from "./fringe";

/**
 * What a lane's layout may spend on antialiased polygon edges, in squared corner counts: about 5 ms of layout, which
 * is one polygon of 1,000 corners, four of 500 or a hundred of 100. Polygons past it keep hard edges.
 */
export const FRINGE_BUDGET = 1_000_000;

/**
 * Polygon fills of a lane as blocks the painter fills through the stencil: corner count, fill (premultiplied RGBA
 * word), the measure (area over perimeter, the polygon's thickness in target pixels), then the vertices. The first
 * `count` vertices are the corners, which fan out for the stencil; the next `2 * count + 2` run around the polygon as
 * a strip of the faded ring, each corner's faded vertex followed by its filled one. The painter moves a vertex by its
 * offset vector times a distance that depends on the device pixel ratio, which the layout does not know.
 */
export class FillWriter {
  private f32 = new Float32Array(1024);
  private u32 = new Uint32Array(this.f32.buffer);
  private offsets = new Float64Array(0);
  private corners: number[] = [];
  /** Floats written. */
  length = 0;
  /** What is left of the layout's budget for corner offsets, in squared corner counts. */
  budget = FRINGE_BUDGET;

  clear(): void {
    this.length = 0;
    this.budget = FRINGE_BUDGET;
  }

  /** The blocks written, viewed in place. */
  view(from = 0, to = this.length): Float32Array {
    return this.f32.subarray(from, to);
  }

  /** Add a block for `count` points from `first` of `points` (x, y fractions of the image) scaled to the target; ignored when a corner is not finite or the polygon has no extent. */
  add(
    points: Float32Array,
    first: number,
    count: number,
    width: number,
    height: number,
    color: number,
  ): void {
    const xy = this.corners;
    xy.length = count * 2;
    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    let area = 0;
    let perimeter = 0;
    for (let corner = 0; corner < count; corner++) {
      const x = points[(first + corner) * 2] * width;
      const y = points[(first + corner) * 2 + 1] * height;
      if (!Number.isFinite(x + y)) return;
      xy[corner * 2] = x;
      xy[corner * 2 + 1] = y;
      left = Math.min(left, x);
      top = Math.min(top, y);
      right = Math.max(right, x);
      bottom = Math.max(bottom, y);
    }
    if (!(right > left && bottom > top)) return;
    for (let corner = 0; corner < count; corner++) {
      const next = (corner + 1) % count;
      area += xy[corner * 2] * xy[next * 2 + 1] - xy[next * 2] * xy[corner * 2 + 1];
      perimeter += Math.hypot(xy[next * 2] - xy[corner * 2], xy[next * 2 + 1] - xy[corner * 2 + 1]);
    }
    if (this.offsets.length < count * CORNER_FLOATS)
      this.offsets = new Float64Array(count * CORNER_FLOATS);
    // The offsets cost the square of the corners, so a layout has a budget of them; a polygon past it keeps hard edges.
    if (count * count <= this.budget) {
      this.budget -= count * count;
      cornerOffsets(xy, count, this.offsets);
    } else this.offsets.fill(0, 0, count * CORNER_FLOATS);
    const needed = this.length + FILL_HEADER_FLOATS + (3 * count + 2) * FILL_VERTEX_FLOATS;
    if (needed > this.f32.length) {
      const grown = new Float32Array(Math.max(needed, this.f32.length * 2));
      grown.set(this.f32.subarray(0, this.length));
      this.f32 = grown;
      this.u32 = new Uint32Array(grown.buffer);
    }
    const f32 = this.f32;
    const at = this.length;
    f32[at] = count;
    this.u32[at + 1] = color;
    f32[at + 2] = Math.abs(area) / 2 / perimeter;
    let out = at + FILL_HEADER_FLOATS;
    const vertex = (corner: number, side: number) => {
      f32[out] = xy[corner * 2];
      f32[out + 1] = xy[corner * 2 + 1];
      f32[out + 2] = this.offsets[corner * CORNER_FLOATS];
      f32[out + 3] = this.offsets[corner * CORNER_FLOATS + 1];
      f32[out + 4] = side;
      f32[out + 5] = this.offsets[corner * CORNER_FLOATS + 2];
      out += FILL_VERTEX_FLOATS;
    };
    for (let corner = 0; corner < count; corner++) vertex(corner, 0);
    for (let corner = 0; corner <= count; corner++) {
      vertex(corner % count, 1);
      vertex(corner % count, 0);
    }
    this.length = needed;
  }
}

/**
 * What a lane's layout leaves for the painter: every quad in draw order, the polygon fills, and where each layer
 * ends in both, so the painter can draw layer after layer (the fills of a layer under its quads).
 */
export class LaneQuads {
  readonly quads = new QuadWriter();
  readonly fills = new FillWriter();
  /** Per layer, the quad count and the fill float count when the layer ended. */
  layerQuads = new Uint32Array(16);
  layerFills = new Uint32Array(16);
  layerCount = 0;

  clear(): void {
    this.quads.clear();
    this.fills.clear();
    this.layerCount = 0;
  }

  /** Close layer `index`. */
  endLayer(index: number): void {
    if (index >= this.layerQuads.length) {
      const size = Math.max(this.layerQuads.length * 2, index + 1);
      const quads = new Uint32Array(size);
      quads.set(this.layerQuads);
      this.layerQuads = quads;
      const fills = new Uint32Array(size);
      fills.set(this.layerFills);
      this.layerFills = fills;
    }
    this.layerQuads[index] = this.quads.count;
    this.layerFills[index] = this.fills.length;
    this.layerCount = index + 1;
  }
}
