import { QuadWriter } from "../../overlay/quads";

/**
 * Polygon fills of a lane as blocks the painter fills through the stencil: corner count, fill (premultiplied RGBA
 * word), left, top, right, bottom, then the corners as x, y pairs.
 */
export class FillWriter {
  private f32 = new Float32Array(1024);
  private u32 = new Uint32Array(this.f32.buffer);
  /** Floats written. */
  length = 0;

  clear(): void {
    this.length = 0;
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
    const needed = this.length + 6 + count * 2;
    if (needed > this.f32.length) {
      const grown = new Float32Array(Math.max(needed, this.f32.length * 2));
      grown.set(this.f32.subarray(0, this.length));
      this.f32 = grown;
      this.u32 = new Uint32Array(grown.buffer);
    }
    const f32 = this.f32;
    const at = this.length;
    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    for (let corner = 0; corner < count; corner++) {
      const x = points[(first + corner) * 2] * width;
      const y = points[(first + corner) * 2 + 1] * height;
      if (!Number.isFinite(x + y)) return;
      f32[at + 6 + corner * 2] = x;
      f32[at + 7 + corner * 2] = y;
      left = Math.min(left, x);
      top = Math.min(top, y);
      right = Math.max(right, x);
      bottom = Math.max(bottom, y);
    }
    if (!(right > left && bottom > top)) return;
    f32[at] = count;
    this.u32[at + 1] = color;
    f32[at + 2] = left;
    f32[at + 3] = top;
    f32[at + 4] = right;
    f32[at + 5] = bottom;
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
