// Generated from export::render by make; do not edit.

import type { SizeWire } from "./foundation.time";

/**
 * How a lane looks, frozen when the export starts.
 */
export type LaneStyleWire = { 
/**
 * The lane's name, written on its cell when several lanes share the picture.
 */
label: string, 
/**
 * The indices, in `LaneStatusWire::Ready.filter`, of the class filters switched on.
 */
enabled_classes: Array<number>, 
/**
 * How many class filters the file offers: the length of `LaneStatusWire::Ready.filter`.
 */
class_options: number, 
/**
 * Bits of the catalog's features switched off.
 */
hidden: number, 
/**
 * One of the catalog's themes; `null` is the catalog's own look.
 */
theme: string | null, 
/**
 * The painter's opacity for samples kept by sticky, 0 to 1.
 */
retained_opacity: number, 
/**
 * The lane's frame filter expression; `null` draws every sample.
 */
filter: string | null, };

/**
 * The pixel layout a renderer reads back.
 */
export type OutputWire = "I420" | "Rgba";

/**
 * One lane the renderer draws.
 */
export type RenderLaneWire = { 
/**
 * The viewer's id of the lane's source, as in the Sample envelopes; `null` for a plain lane.
 */
source: number | null, 
/**
 * The catalog document text.
 */
catalog: string, 
/**
 * How the lane looks.
 */
style: LaneStyleWire, };

/**
 * The job, once, before the first frame.
 */
export type RenderSetup = { 
/**
 * What to read back.
 */
output: OutputWire, 
/**
 * The size of the video's pictures.
 */
picture: SizeWire, 
/**
 * The largest output the export accepts; a page that wants more fails the setup.
 */
max_output: SizeWire, 
/**
 * The lanes, in the order they tile.
 */
lanes: Array<RenderLaneWire>, };
