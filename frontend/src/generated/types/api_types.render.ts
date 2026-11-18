// Generated from api_types::render by make; do not edit.

import type { RenderSetup } from "./export.render";

/**
 * What the backend sends as text, besides the binary envelopes.
 */
export type HostMessage = { "type": "Setup" } & RenderSetup | { "type": "Render", 
/**
 * Echoed in the Pixels message.
 */
seq: number, 
/**
 * The sample index each lane draws, -1 when nothing belongs to the frame or -2 when a dense file covers it with
 * nothing (a Frame Map cell read through an `Int32Array`).
 */
cells: Array<number>, 
/**
 * The `MATCH_*` kind of each lane's cell.
 */
kinds: Array<number>, };

/**
 * What the page sends as text.
 */
export type PageMessage = { "type": "Hello", 
/**
 * The protocol version, to compare with `RENDER_PROTOCOL`.
 */
protocol: number, 
/**
 * The secret the backend gave the page process.
 */
secret: string, } | { "type": "Ready", 
/**
 * Width of the output; even for I420.
 */
width: number, 
/**
 * Height of the output; even for I420.
 */
height: number, 
/**
 * The GPU or software renderer in use, for the log.
 */
renderer: string, } | { "type": "Failed", 
/**
 * What went wrong, in words for the user.
 */
reason: string, };

/**
 * Raised when the messages or the Pixels layout change in a way an older render page cannot use.
 */
export const RENDER_PROTOCOL = 1;

/**
 * "AXPX" read as a little-endian u32.
 */
export const PIXELS_MAGIC = 1481660481;

/**
 * Bytes before the planes of a Pixels message.
 */
export const PIXELS_HEADER_BYTES = 32;

/**
 * Pixels format: planar Y, U, V with 4:2:0 subsampling, BT.709 limited range.
 */
export const PIXELS_I420 = 1;

/**
 * Pixels format: one interleaved RGBA plane.
 */
export const PIXELS_RGBA = 2;
