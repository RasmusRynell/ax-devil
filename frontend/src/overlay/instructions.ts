/**
 * The Drawing Instructions of one Overlay envelope (kind 2), decoded into typed views over the received bytes without
 * copying them. The section layout is documented in crates/api-types/src/playback.rs: every record is a run of
 * 4-byte little-endian words, so each table is one word array seen as floats, as unsigned integers and as bytes.
 */

import {
  OVERLAY_TAG_BOXES,
  OVERLAY_TAG_CIRCLES,
  OVERLAY_TAG_LABEL_STYLES,
  OVERLAY_TAG_LABELS,
  OVERLAY_TAG_LINES,
  OVERLAY_TAG_PAINTS,
  OVERLAY_TAG_PATH_POINTS,
  OVERLAY_TAG_POINTS,
  OVERLAY_TAG_POLYGONS,
  OVERLAY_TAG_POLYLINES,
  OVERLAY_TAG_RUNS,
  OVERLAY_TAG_STRINGS,
  OVERLAY_TAG_TEXTS,
} from "../generated/api";
import { EnvelopeError } from "../playback/protocol";

/** An envelope's sections by tag, each viewing the received message. Unknown tags are left in and ignored. */
export type OverlaySections = ReadonlyMap<number, DataView>;

/** Fixed-size records of 4-byte words; record `i` starts at word `i * stride`. */
export type Table = {
  readonly count: number;
  readonly stride: number;
  readonly f32: Float32Array;
  readonly u32: Uint32Array;
  /** Byte fields (colors, flags, anchors), four per word. */
  readonly u8: Uint8Array;
};

/** One lane frame's Drawing Instructions, as `drawing::DrawingInstructions` in the backend has them. */
export interface DecodedInstructions {
  /** pen rgb + dashed bytes, pen_width, brush rgba bytes, font_family, font_size, radius. */
  readonly paints: Table;
  /** x, y, w, h, paint. */
  readonly boxes: Table;
  /** x1, y1, x2, y2, paint. */
  readonly lines: Table;
  /** x, y, radius, paint. */
  readonly circles: Table;
  /** x, y, paint. */
  readonly points: Table;
  /** x, y. */
  readonly pathPoints: Table;
  /** first, count, paint, into `pathPoints`. */
  readonly polygons: Table;
  /** first, count, paint, into `pathPoints`. */
  readonly polylines: Table;
  /** x, y, text, anchor byte, paint. */
  readonly texts: Table;
  /** size, family, background rgba bytes, padding_x, padding_y, radius, gap. */
  readonly labelStyles: Table;
  /** x, y, anchor byte, style, first_run, runs. */
  readonly labels: Table;
  /** text, bar (NaN for text), rgb + weight bytes. */
  readonly runs: Table;
  /** The string table; ids index it, and an id past its end (such as `NO_STRING`) is no string. */
  readonly strings: readonly string[];
}

/** The string id that means none, such as the default font family. */
export const NO_STRING = 0xffff_ffff;

/** Words per record of each table. */
export const STRIDE = {
  paints: 6,
  boxes: 5,
  lines: 5,
  circles: 4,
  points: 3,
  pathPoints: 2,
  paths: 3,
  texts: 5,
  labelStyles: 7,
  labels: 6,
  runs: 3,
} as const;

/** Where the anchor point lies on a text or label box, as fractions of its width and height, by wire anchor code. */
const ANCHOR_FACTORS = new Float32Array([
  0, 0, 0.5, 0, 1, 0, 0, 0.5, 0.5, 0.5, 1, 0.5, 0, 1, 0.5, 1, 1, 1, 0.5, 0, 0.5, 1, 0, 0.5, 1, 0.5,
]);

/** The fraction of the box's width (`axis` 0) or height (1) left of or above the anchor; unknown codes are top-left. */
export function anchorFactor(code: number, axis: 0 | 1): number {
  return code < 13 ? ANCHOR_FACTORS[code * 2 + axis] : 0;
}

// Typed arrays read in the platform's byte order; every platform a browser runs on is little-endian.
const LITTLE_ENDIAN = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;
const EMPTY = new ArrayBuffer(0);
const utf8 = new TextDecoder();

function table(sections: OverlaySections, tag: number, stride: number): Table {
  const view = sections.get(tag);
  const bytes = view?.byteLength ?? 0;
  if (bytes % (stride * 4) !== 0)
    throw new EnvelopeError(`overlay section ${tag} is not a whole number of records`);
  let buffer: ArrayBufferLike = view?.buffer ?? EMPTY;
  let offset = view?.byteOffset ?? 0;
  if (view && offset % 4 !== 0) {
    // Word views need 4-byte alignment; a misaligned section is copied once.
    buffer = new Uint8Array(view.buffer, offset, bytes).slice().buffer;
    offset = 0;
  }
  return {
    count: bytes / (stride * 4),
    stride,
    f32: new Float32Array(buffer, offset, bytes / 4),
    u32: new Uint32Array(buffer, offset, bytes / 4),
    u8: new Uint8Array(buffer, offset, bytes),
  };
}

function strings(view: DataView | undefined): string[] {
  if (!view || view.byteLength === 0) return [];
  if (view.byteLength < 8) throw new EnvelopeError("overlay strings section is too short");
  const count = view.getUint32(0, true);
  const start = 4 + (count + 1) * 4;
  if (start > view.byteLength) throw new EnvelopeError("overlay string offsets are truncated");
  const bytes = new Uint8Array(view.buffer, view.byteOffset + start, view.byteLength - start);
  const out: string[] = [];
  let from = view.getUint32(4, true);
  for (let index = 0; index < count; index++) {
    const to = view.getUint32(8 + index * 4, true);
    if (to < from || to > bytes.length)
      throw new EnvelopeError("overlay string offsets are out of order");
    out.push(utf8.decode(bytes.subarray(from, to)));
    from = to;
  }
  return out;
}

/**
 * Views an Overlay's sections as tables. Throws `EnvelopeError` when a section is not a whole number of records or
 * the string table is malformed; rows that point past a table are left for preparation to skip.
 */
export function decodeInstructions(sections: OverlaySections): DecodedInstructions {
  if (!LITTLE_ENDIAN) throw new EnvelopeError("overlays need a little-endian platform");
  return {
    paints: table(sections, OVERLAY_TAG_PAINTS, STRIDE.paints),
    boxes: table(sections, OVERLAY_TAG_BOXES, STRIDE.boxes),
    lines: table(sections, OVERLAY_TAG_LINES, STRIDE.lines),
    circles: table(sections, OVERLAY_TAG_CIRCLES, STRIDE.circles),
    points: table(sections, OVERLAY_TAG_POINTS, STRIDE.points),
    pathPoints: table(sections, OVERLAY_TAG_PATH_POINTS, STRIDE.pathPoints),
    polygons: table(sections, OVERLAY_TAG_POLYGONS, STRIDE.paths),
    polylines: table(sections, OVERLAY_TAG_POLYLINES, STRIDE.paths),
    texts: table(sections, OVERLAY_TAG_TEXTS, STRIDE.texts),
    labelStyles: table(sections, OVERLAY_TAG_LABEL_STYLES, STRIDE.labelStyles),
    labels: table(sections, OVERLAY_TAG_LABELS, STRIDE.labels),
    runs: table(sections, OVERLAY_TAG_RUNS, STRIDE.runs),
    strings: strings(sections.get(OVERLAY_TAG_STRINGS)),
  };
}
