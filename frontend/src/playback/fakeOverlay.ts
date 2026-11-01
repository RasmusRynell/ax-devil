// Overlays as the backend sends them and as the worker hands them over, for tests.
import { ENVELOPE_MAGIC, OVERLAY_HEADER_BYTES } from "./protocol";
import type { AtlasUpload } from "../overlay/atlas";
import type { PreparedDrawing } from "../overlay/prepare";

/** One kind 2 message: the 64 byte header the format documents, then `sections` (tag and bytes) padded to 8. */
export function overlayBytes(
  fields: {
    lane?: number;
    sequenceId?: number;
    revision?: number;
    match?: number;
    opacity?: number;
    target?: { width: number; height: number };
    sampleKey?: number;
    sections?: [number, Uint8Array][];
  } = {},
): ArrayBuffer {
  const sections = fields.sections ?? [];
  const padded = (length: number) => Math.ceil(length / 8) * 8;
  const size = OVERLAY_HEADER_BYTES + sections.reduce((n, [, b]) => n + 8 + padded(b.length), 0);
  const bytes = new Uint8Array(size);
  const view = new DataView(bytes.buffer);
  view.setUint32(0, ENVELOPE_MAGIC, true);
  view.setUint8(4, 1);
  view.setUint8(5, 2);
  view.setUint16(6, OVERLAY_HEADER_BYTES, true);
  view.setBigUint64(8, BigInt(fields.sequenceId ?? 0), true);
  view.setUint16(28, fields.lane ?? 0, true);
  view.setUint8(30, fields.match ?? 1);
  view.setUint32(32, fields.revision ?? 0, true);
  view.setFloat32(36, fields.opacity ?? 1, true);
  view.setUint32(40, fields.target?.width ?? 640, true);
  view.setUint32(44, fields.target?.height ?? 480, true);
  view.setBigInt64(48, BigInt(fields.sampleKey ?? 0), true);
  view.setUint32(56, sections.length, true);
  let at = OVERLAY_HEADER_BYTES;
  for (const [tag, data] of sections) {
    view.setUint16(at, tag, true);
    view.setUint32(at + 4, data.length, true);
    bytes.set(data, at + 8);
    at += 8 + padded(data.length);
  }
  return bytes.buffer;
}

/** A BOXES section (tag 2) of `count` boxes. */
export function boxesSection(count: number): [number, Uint8Array] {
  const data = new Uint8Array(count * 20);
  const view = new DataView(data.buffer);
  for (let index = 0; index < count; index++) {
    [0.1, 0.1, 0.5, 0.5].forEach((value, at) => view.setFloat32(index * 20 + at * 4, value, true));
  }
  return [2, data];
}

/** A prepared drawing with no shapes, on atlas page `generation`. */
export function preparedDrawing(generation = 0, uploads: AtlasUpload[] = []): PreparedDrawing {
  return {
    generation,
    shapes: new Float32Array(0),
    texts: new Float32Array(0),
    polygons: new Float32Array(0),
    uploads,
    opacity: 1,
    target: { width: 640, height: 480 },
    dpr: 1,
  };
}
