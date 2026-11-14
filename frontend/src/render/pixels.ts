/**
 * The Pixels message the page answers a Render with (layout in crates/api-types/src/render.rs) and the byte layout of
 * the I420 planes in it.
 */

import {
  PIXELS_HEADER_BYTES,
  PIXELS_I420,
  PIXELS_MAGIC,
  PIXELS_RGBA,
  type OutputWire,
} from "../generated/api";

/** Where the planes of one picture go. Rows are padded to four bytes so the pack pass can write whole texels. */
type PlaneLayout = {
  format: number;
  width: number;
  height: number;
  /** Bytes per row of the first plane, and of each chroma plane (0 for RGBA). */
  stride0: number;
  stride1: number;
  /** Bytes of all planes. */
  bytes: number;
};

/**
 * The layout of a `width` by `height` picture. For I420 (both even) a chroma row is half a luma row, so the planes
 * are one run of `stride0` bytes by 1.5 `height` rows: that is what the pack pass renders and reads back whole.
 */
export function planesOf(output: OutputWire, width: number, height: number): PlaneLayout {
  if (output === "Rgba")
    return {
      format: PIXELS_RGBA,
      width,
      height,
      stride0: width * 4,
      stride1: 0,
      bytes: width * height * 4,
    };
  const stride1 = Math.ceil(width / 2 / 4) * 4;
  const stride0 = stride1 * 2;
  return {
    format: PIXELS_I420,
    width,
    height,
    stride0,
    stride1,
    bytes: (stride0 * height * 3) / 2,
  };
}

/** One reusable Pixels message: the header is rewritten for each answer, the planes are filled by the caller. */
export class PixelsMessage {
  /** The whole message. */
  readonly bytes: Uint8Array<ArrayBuffer>;
  /** The planes in it. */
  readonly planes: Uint8Array<ArrayBuffer>;
  readonly #view: DataView;

  constructor(readonly layout: PlaneLayout) {
    this.bytes = new Uint8Array(PIXELS_HEADER_BYTES + layout.bytes);
    this.planes = this.bytes.subarray(PIXELS_HEADER_BYTES);
    this.#view = new DataView(this.bytes.buffer);
    this.#view.setUint32(0, PIXELS_MAGIC, true);
    this.#view.setUint32(4, layout.format, true);
    this.#view.setUint32(16, layout.width, true);
    this.#view.setUint32(20, layout.height, true);
    this.#view.setUint32(24, layout.stride0, true);
    this.#view.setUint32(28, layout.stride1, true);
  }

  /** Marks the message as the answer to Render `seq`. */
  answer(seq: number): Uint8Array<ArrayBuffer> {
    this.#view.setBigUint64(8, BigInt(seq), true);
    return this.bytes;
  }
}
