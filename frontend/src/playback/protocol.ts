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
  KIND_OVERLAY,
  KIND_TIMELINE,
  KIND_VIDEO_FRAME,
  LAYOUT_I420,
  LAYOUT_NV12,
  LAYOUT_RGBA,
  MATRIX_BT601,
  MATRIX_BT709,
  OVERLAY_HEADER_BYTES,
  OVERLAY_MATCH_EMPTY,
  OVERLAY_MATCH_EXACT,
  OVERLAY_MATCH_NONE,
  OVERLAY_MATCH_RETAINED,
  OVERLAY_MATCH_TOLERATED,
  VIDEO_FRAME_HEADER_BYTES,
} from "../generated/api";
import type { OverlaySections } from "../overlay/instructions";

// The constants and the control message types are generated from crates/api-types; every other module imports
// them from here.
export {
  ENVELOPE_HEAD_BYTES,
  ENVELOPE_MAGIC,
  ENVELOPE_VERSION,
  FLAG_KEYFRAME,
  FLAG_LAST_FRAME,
  KIND_OVERLAY,
  KIND_TIMELINE,
  KIND_VIDEO_FRAME,
  OVERLAY_HEADER_BYTES,
  PLAYBACK_PROTOCOL,
} from "../generated/api";
export type {
  CatalogInfoWire,
  ClientMessage,
  ClockWire,
  Direction,
  FilterOptionInfo,
  LaneInfo,
  LaneStatusWire,
  LaneViewWire,
  ServerMessage,
  StickyWire,
  TargetWire,
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

/** How an overlay's sample was found for its frame. */
export type MatchKind = "none" | "exact" | "tolerated" | "retained" | "empty";

/** One lane's Drawing Instructions for one frame and view revision; the sections view the received message. */
export type OverlayEnvelope = {
  kind: "overlay";
  lane: number;
  sequenceId: number;
  revision: number;
  match: MatchKind;
  /** Below 1 for a sticky sample kept from an earlier frame. */
  opacity: number;
  /** The displayed image size in CSS pixels that the lane's catalog was evaluated for. */
  target: { width: number; height: number };
  /** The sample's frame number or microseconds by the lane's clock; 0 when nothing matched. */
  sampleKey: number;
  sections: OverlaySections;
};

export type Envelope =
  | { kind: "frame"; frame: VideoFrame }
  | OverlayEnvelope
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
const MATCHES: Record<number, MatchKind> = {
  [OVERLAY_MATCH_NONE]: "none",
  [OVERLAY_MATCH_EXACT]: "exact",
  [OVERLAY_MATCH_TOLERATED]: "tolerated",
  [OVERLAY_MATCH_RETAINED]: "retained",
  [OVERLAY_MATCH_EMPTY]: "empty",
};
/** Bytes before a section's data: tag, padding and length. */
const SECTION_HEAD_BYTES = 8;

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

  if (kind === KIND_OVERLAY) return parseOverlay(view, bytes, payload);

  return null;
}

/** The overlay header and sections of a kind 2 message. */
function parseOverlay(view: DataView, bytes: Uint8Array, payload: number): OverlayEnvelope {
  if (payload < OVERLAY_HEADER_BYTES) throw new EnvelopeError("overlay header too short");
  const match = MATCHES[view.getUint8(30)];
  if (match === undefined) throw new EnvelopeError(`unknown overlay match ${view.getUint8(30)}`);
  const sections = new Map<number, DataView>();
  let at = payload;
  for (let index = view.getUint32(56, true); index > 0; index--) {
    if (at + SECTION_HEAD_BYTES > bytes.length)
      throw new EnvelopeError("overlay sections are truncated");
    const length = view.getUint32(at + 4, true);
    const start = at + SECTION_HEAD_BYTES;
    if (start + length > bytes.length) throw new EnvelopeError("an overlay section is truncated");
    // Every section is kept by tag; the instruction decoder ignores the tags it does not know.
    sections.set(
      view.getUint16(at, true),
      new DataView(bytes.buffer, bytes.byteOffset + start, length),
    );
    at = start + Math.ceil(length / 8) * 8;
  }
  return {
    kind: "overlay",
    lane: view.getUint16(28, true),
    sequenceId: readU64(view, 8),
    revision: view.getUint32(32, true),
    match,
    opacity: view.getFloat32(36, true),
    target: { width: view.getUint32(40, true), height: view.getUint32(44, true) },
    sampleKey: readI64(view, 48),
    sections,
  };
}
