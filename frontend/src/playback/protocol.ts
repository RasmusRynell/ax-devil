/**
 * The playback WebSocket protocol, client side: JSON control message types and a hand-written parser for the binary
 * envelope. The envelope layout (little-endian) is documented in crates/api-types/src/playback.rs.
 */

import {
  ENVELOPE_HEAD_BYTES,
  ENVELOPE_MAGIC,
  ENVELOPE_VERSION,
  FLAG_KEYFRAME,
  FLAG_LAST_FRAME,
  FLAG_SAMPLE_TIME,
  FRAME_MAP_HEADER_BYTES,
  FRAME_MAP_RUN_FRAMES,
  KIND_FRAME_MAP,
  KIND_SAMPLE,
  KIND_TIMELINE,
  KIND_VIDEO_FRAME,
  LAYOUT_I420,
  LAYOUT_NV12,
  LAYOUT_RGBA,
  MATRIX_BT601,
  MATRIX_BT709,
  SAMPLE_HEADER_BYTES,
  SAMPLE_NO_FRAME,
  VIDEO_FRAME_HEADER_BYTES,
} from "../generated/api";

// The constants and the control message types are generated from crates/api-types; every other module imports
// them from here.
export {
  ENVELOPE_HEAD_BYTES,
  ENVELOPE_MAGIC,
  ENVELOPE_VERSION,
  FLAG_KEYFRAME,
  FLAG_LAST_FRAME,
  FLAG_SAMPLE_TIME,
  FRAME_MAP_EMPTY,
  FRAME_MAP_HEADER_BYTES,
  FRAME_MAP_NONE,
  FRAME_MAP_RUN_FRAMES,
  KIND_FRAME_MAP,
  KIND_SAMPLE,
  KIND_TIMELINE,
  KIND_VIDEO_FRAME,
  MATCH_EXACT,
  MATCH_RETAINED,
  MATCH_TOLERATED,
  MAX_DATA_WINDOW_FRAMES,
  PLAYBACK_PROTOCOL,
  SAMPLE_HEADER_BYTES,
  SAMPLE_NO_FRAME,
} from "../generated/api";
export type {
  CatalogInfoWire,
  BasisWire,
  ClientMessage,
  Direction,
  FieldGroupWire,
  FieldOriginWire,
  FieldWire,
  FilterOptionInfo,
  InspectionWire,
  LaneDefaultsWire,
  LaneInfo,
  LanePolicyWire,
  LaneStatusWire,
  ServerMessage,
  StickyWire,
  Want,
  WarningInfo,
} from "../generated/api";

/** How the planes of a frame are laid out; each frame says so in its header. */
export type PixelFormat = "i420" | "nv12" | "rgba";
export type Matrix = "bt601" | "bt709";

/** One decoded frame as sent: transport-ready planes, viewing the received message without copying. */
export type VideoFrame = {
  sequenceId: number;
  timestampUs: number;
  keyframe: boolean;
  last: boolean;
  width: number;
  height: number;
  format: PixelFormat;
  matrix: Matrix;
  fullRange: boolean;
  /** Always three entries; an unused plane has length 0. */
  strides: [number, number, number];
  planes: [Uint8Array, Uint8Array, Uint8Array];
  /** Plane bytes, what the frame buffer counts against its budget. */
  byteLength: number;
};

/** One Sample Item of one source; `item` views the received message from the item's first byte. */
export type SampleEnvelope = {
  kind: "sample";
  /** The viewer's source id (`Opened.lanes[i].source`). */
  source: number;
  /** The source revision the item belongs to. */
  dataRevision: number;
  /** The sample's index in its source. */
  sample: number;
  /** The sample's time on the video clock in microseconds; `null` when the backend has none. */
  timeUs: number | null;
  /** The sample's frame number in its file; `null` when it has none. */
  frame: number | null;
  item: Uint8Array;
};

/** One run of one lane's Frame Map: `samples[k]` and `kinds[k]` belong to frame `first + k`. */
export type FrameMapEnvelope = {
  kind: "map";
  lane: number;
  /** The Lane Policy revision the run was selected under. */
  policyRevision: number;
  dataRevision: number;
  first: number;
  /** A sample index, `FRAME_MAP_NONE` or `FRAME_MAP_EMPTY` as `Int32Array` reads them (-1 and -2). */
  samples: Int32Array;
  /** `MATCH_*` per frame. */
  kinds: Uint8Array;
};

export type Envelope =
  | { kind: "frame"; frame: VideoFrame }
  | SampleEnvelope
  | FrameMapEnvelope
  /** Presentation time in microseconds by sequence id. */
  | { kind: "timeline"; times: Float64Array };

/** A binary message that breaks the envelope format. The session ends: the two sides disagree on the protocol. */
export class EnvelopeError extends Error {
  constructor(reason: string) {
    super(reason);
    this.name = "EnvelopeError";
  }
}

const FORMATS: Record<number, PixelFormat> = {
  [LAYOUT_I420]: "i420",
  [LAYOUT_NV12]: "nv12",
  [LAYOUT_RGBA]: "rgba",
};
const MATRICES: Record<number, Matrix> = { [MATRIX_BT601]: "bt601", [MATRIX_BT709]: "bt709" };
/** Bytes of one tightly packed transport frame. */
export function frameBytes(width: number, height: number, format: PixelFormat): number {
  if (format === "rgba") return width * height * 4;
  return width * height + 2 * Math.ceil(width / 2) * Math.ceil(height / 2);
}

function readU64(view: DataView, offset: number): number {
  const high = view.getUint32(offset + 4, true);
  if (high >= 0x200000) throw new EnvelopeError("a 64-bit value exceeds 2^53");
  return high * 2 ** 32 + view.getUint32(offset, true);
}

function readI64(view: DataView, offset: number): number {
  const high = view.getInt32(offset + 4, true);
  if (high < -0x200000 || high >= 0x200000) throw new EnvelopeError("a 64-bit value exceeds 2^53");
  return high * 2 ** 32 + view.getUint32(offset, true);
}

/**
 * Parses one binary message. Returns `null` for kinds this client ignores (kinds from a newer backend); throws
 * `EnvelopeError` for anything malformed.
 */
export function parseEnvelope(data: ArrayBufferLike | ArrayBufferView): Envelope | null {
  const bytes = ArrayBuffer.isView(data)
    ? new Uint8Array(data.buffer, data.byteOffset, data.byteLength)
    : new Uint8Array(data);
  if (bytes.length < ENVELOPE_HEAD_BYTES)
    throw new EnvelopeError("message shorter than the envelope head");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(0, true) !== ENVELOPE_MAGIC) throw new EnvelopeError("bad envelope magic");
  if (view.getUint8(4) !== ENVELOPE_VERSION)
    throw new EnvelopeError(`unsupported envelope version ${view.getUint8(4)}`);
  const kind = view.getUint8(5);
  // header_len is the offset of the payload from the start of the message.
  const payload = view.getUint16(6, true);
  if (payload < ENVELOPE_HEAD_BYTES || payload > bytes.length)
    throw new EnvelopeError("bad header length");

  if (kind === KIND_VIDEO_FRAME) {
    if (payload < VIDEO_FRAME_HEADER_BYTES) throw new EnvelopeError("video header too short");
    const format = FORMATS[view.getUint8(36)];
    const matrix = MATRICES[view.getUint8(37)];
    if (format === undefined) throw new EnvelopeError(`unknown pixel layout ${view.getUint8(36)}`);
    if (matrix === undefined) throw new EnvelopeError(`unknown color matrix ${view.getUint8(37)}`);
    const flags = view.getUint32(24, true);
    const strides: [number, number, number] = [
      view.getUint32(40, true),
      view.getUint32(44, true),
      view.getUint32(48, true),
    ];
    const lengths = [view.getUint32(52, true), view.getUint32(56, true), view.getUint32(60, true)];
    const byteLength = lengths[0] + lengths[1] + lengths[2];
    if (payload + byteLength > bytes.length) throw new EnvelopeError("frame planes are truncated");
    const planeAt = (start: number, length: number) =>
      bytes.subarray(payload + start, payload + start + length);
    return {
      kind: "frame",
      frame: {
        sequenceId: readU64(view, 8),
        timestampUs: readI64(view, 16),
        keyframe: (flags & FLAG_KEYFRAME) !== 0,
        last: (flags & FLAG_LAST_FRAME) !== 0,
        width: view.getUint32(28, true),
        height: view.getUint32(32, true),
        format,
        matrix,
        fullRange: view.getUint8(38) !== 0,
        strides,
        planes: [
          planeAt(0, lengths[0]),
          planeAt(lengths[0], lengths[1]),
          planeAt(lengths[0] + lengths[1], lengths[2]),
        ],
        byteLength,
      },
    };
  }

  if (kind === KIND_TIMELINE) {
    const length = bytes.length - payload;
    if (length % 8 !== 0)
      throw new EnvelopeError("timeline length is not a whole number of float64 values");
    const start = bytes.byteOffset + payload;
    // A Float64Array view needs 8-byte alignment; a message that arrives misaligned is copied once.
    const times =
      start % 8 === 0
        ? new Float64Array(bytes.buffer, start, length / 8)
        : new Float64Array(bytes.slice(payload).buffer);
    return { kind: "timeline", times };
  }

  if (kind === KIND_SAMPLE) return parseSample(view, bytes, payload);

  if (kind === KIND_FRAME_MAP) return parseFrameMap(view, bytes, payload);

  return null;
}

/** The header of a kind 4 message; the Sample Item to the end of the message is passed on as it is. */
function parseSample(view: DataView, bytes: Uint8Array, payload: number): SampleEnvelope {
  if (payload !== SAMPLE_HEADER_BYTES) throw new EnvelopeError("bad sample header length");
  if ((bytes.length - payload) % 8 !== 0)
    throw new EnvelopeError("sample item length is not a multiple of 8");
  const frame = view.getUint32(36, true);
  return {
    kind: "sample",
    source: view.getUint16(28, true),
    dataRevision: view.getUint32(32, true),
    sample: readU64(view, 8),
    timeUs: (view.getUint32(24, true) & FLAG_SAMPLE_TIME) !== 0 ? readI64(view, 16) : null,
    frame: frame === SAMPLE_NO_FRAME ? null : frame,
    item: bytes.subarray(payload),
  };
}

/** The header and cells of a kind 5 message. */
function parseFrameMap(view: DataView, bytes: Uint8Array, payload: number): FrameMapEnvelope {
  if (payload !== FRAME_MAP_HEADER_BYTES) throw new EnvelopeError("bad frame map header length");
  const count = view.getUint32(40, true);
  if (count < 1 || count > FRAME_MAP_RUN_FRAMES)
    throw new EnvelopeError(`a frame map run of ${count} frames`);
  if (bytes.length !== payload + 5 * count)
    throw new EnvelopeError("frame map length does not match its frame count");
  const first = readU64(view, 8);
  if ((first % FRAME_MAP_RUN_FRAMES) + count > FRAME_MAP_RUN_FRAMES)
    throw new EnvelopeError("a frame map run crosses a chunk boundary");
  // An Int32Array view needs 4-byte alignment; a message that arrives misaligned is copied once.
  const aligned = bytes.byteOffset % 4 === 0 ? bytes : bytes.slice();
  return {
    kind: "map",
    lane: view.getUint16(28, true),
    policyRevision: view.getUint32(32, true),
    dataRevision: view.getUint32(36, true),
    first,
    samples: new Int32Array(aligned.buffer, aligned.byteOffset + payload, count),
    kinds: aligned.subarray(payload + 4 * count),
  };
}
