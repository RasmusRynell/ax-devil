// Sample Items and Frame Maps as the backend sends them, for tests: real items from the oracle fixtures in envelopes
// built from the wire constants.
import { readFileSync } from "node:fs";
import {
  ENVELOPE_MAGIC,
  FRAME_MAP_EMPTY,
  FRAME_MAP_HEADER_BYTES,
  FRAME_MAP_NONE,
  KIND_FRAME_MAP,
  KIND_SAMPLE,
  SAMPLE_HEADER_BYTES,
} from "./protocol";
import {
  KIND_VIDEO_FRAME,
  LAYOUT_RGBA,
  MATRIX_BT709,
  VIDEO_FRAME_HEADER_BYTES,
} from "../generated/api";

const ROOT = new URL("../../../", import.meta.url);

/** A map cell that belongs to no sample, as `mapBytes` takes it. */
export const NONE = FRAME_MAP_NONE;
/** A map cell of a dense file that covers the frame with nothing. */
export const EMPTY = FRAME_MAP_EMPTY;

/** The Sample Item `fixtures/oracle/items/<name>.bin`, as the Rust encoder wrote it. */
export function item(name = "classes"): Uint8Array {
  return new Uint8Array(readFileSync(new URL(`fixtures/oracle/items/${name}.bin`, ROOT)));
}

/** The catalog document of a built-in catalog, such as `standard`. */
export function catalogDocument(name = "standard"): string {
  return readFileSync(new URL(`assets/catalogs/${name}.v4.json`, ROOT), "utf8");
}

function head(kind: number, headerLen: number, length: number, sequenceId: number): DataView {
  const view = new DataView(new ArrayBuffer(length));
  view.setUint32(0, ENVELOPE_MAGIC, true);
  view.setUint8(4, 1);
  view.setUint8(5, kind);
  view.setUint16(6, headerLen, true);
  view.setBigUint64(8, BigInt(sequenceId), true);
  return view;
}

/** One kind 4 message: the 40 byte header, then `bytes`. */
export function sampleBytes(
  fields: { source?: number; dataRevision?: number; sample?: number; item?: Uint8Array } = {},
): ArrayBuffer {
  const bytes = fields.item ?? item();
  const view = head(
    KIND_SAMPLE,
    SAMPLE_HEADER_BYTES,
    SAMPLE_HEADER_BYTES + bytes.length,
    fields.sample ?? 0,
  );
  view.setUint16(28, fields.source ?? 0, true);
  view.setUint32(32, fields.dataRevision ?? 1, true);
  new Uint8Array(view.buffer).set(bytes, SAMPLE_HEADER_BYTES);
  return view.buffer as ArrayBuffer;
}

/** One kind 5 message for `cells` (sample indices, `NONE` or `EMPTY`) of the frames from `first`. */
export function mapBytes(fields: {
  lane?: number;
  policyRevision?: number;
  dataRevision?: number;
  first?: number;
  cells: readonly number[];
  kinds?: readonly number[];
}): ArrayBuffer {
  const { cells } = fields;
  const view = head(
    KIND_FRAME_MAP,
    FRAME_MAP_HEADER_BYTES,
    FRAME_MAP_HEADER_BYTES + 5 * cells.length,
    fields.first ?? 0,
  );
  view.setUint16(28, fields.lane ?? 0, true);
  view.setUint32(32, fields.policyRevision ?? 1, true);
  view.setUint32(36, fields.dataRevision ?? 1, true);
  view.setUint32(40, cells.length, true);
  cells.forEach((cell, k) => view.setUint32(FRAME_MAP_HEADER_BYTES + 4 * k, cell, true));
  const kinds = new Uint8Array(view.buffer, FRAME_MAP_HEADER_BYTES + 4 * cells.length);
  kinds.set(fields.kinds ?? []);
  return view.buffer as ArrayBuffer;
}

/** One kind 1 message: an RGBA picture of `width` by `height`, from `rgba` (4 bytes a pixel, rows top-down). */
export function frameMessage(
  width: number,
  height: number,
  rgba: Uint8Array,
  sequence = 0,
): ArrayBuffer {
  const view = head(
    KIND_VIDEO_FRAME,
    VIDEO_FRAME_HEADER_BYTES,
    VIDEO_FRAME_HEADER_BYTES + rgba.length,
    sequence,
  );
  view.setUint32(28, width, true);
  view.setUint32(32, height, true);
  view.setUint8(36, LAYOUT_RGBA);
  view.setUint8(37, MATRIX_BT709);
  view.setUint8(38, 1);
  view.setUint32(40, width * 4, true);
  view.setUint32(52, rgba.length, true);
  new Uint8Array(view.buffer).set(rgba, VIDEO_FRAME_HEADER_BYTES);
  return view.buffer as ArrayBuffer;
}
