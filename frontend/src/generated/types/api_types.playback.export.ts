// Generated from api_types::playback::export by make; do not edit.

import type { ErrorWire } from "./api_types.error";
import type { LaneStyleWire } from "./export.render";

/**
 * A catalog document as the backend served it to the viewer: the export draws with exactly that text.
 */
export type CatalogRefWire = { 
/**
 * The catalog id, such as `built-in:standard`.
 */
id: string, 
/**
 * The revision of the document the viewer was served.
 */
revision: number, };

/**
 * What an export writes.
 */
export type ExportFormatWire = { "format": "Mp4", 
/**
 * The quality preset.
 */
quality: ExportQualityWire, } | { "format": "Png" };

/**
 * One lane an export draws.
 */
export type ExportLaneWire = { 
/**
 * The lane's position in `Opened.lanes`.
 */
lane: number, 
/**
 * The catalog the lane draws with.
 */
catalog: CatalogRefWire, 
/**
 * How the lane looks.
 */
style: LaneStyleWire, 
/**
 * The lane's policy revision; the backend refuses the export when it differs from its own.
 */
policy_revision: number, 
/**
 * The source revision the viewer holds, absent for a plain lane; the backend refuses the export when it differs.
 */
data_revision: number | null, };

/**
 * How good the video is: H.264 constant quality, from the largest file to the smallest.
 */
export type ExportQualityWire = "lossless" | "high" | "balanced" | "small";

/**
 * Where an export job stands.
 */
export type ExportStateWire = { "state": "Running", 
/**
 * Frames written.
 */
done: number, 
/**
 * Frames in the range.
 */
total: number, } | { "state": "Done" } | { "state": "Cancelled" } | { "state": "Failed" } & ErrorWire;
