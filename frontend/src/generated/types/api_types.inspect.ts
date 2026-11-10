// Generated from api_types::inspect by make; do not edit.

import type { FieldGroupWire, FieldOriginWire } from "./scene.inspection";
import type { LeafWire } from "./scene.inspection.leaf";

/**
 * One leaf of an object's data.
 */
export type FieldWire = { 
/**
 * The section the field is listed in.
 */
group: FieldGroupWire, 
/**
 * Who produced the field; only in the Debug group.
 */
origin?: FieldOriginWire, 
/**
 * The field's place in the data; a list index is its decimal digits.
 */
path: Array<string>, 
/**
 * The leaf: an integer exactly, a float, text, true or false, or null.
 */
value: LeafWire, 
/**
 * The colour the value is the score of, as red, green and blue; for an entry of a colour attribute.
 */
rgb?: [number, number, number], 
/**
 * What to show in place of the value: a score or confidence to two decimals. Copying takes the value.
 */
text?: string, 
/**
 * The value of a score, a confidence or a colour's score within 0 to 1, drawn as a bar.
 */
fraction?: number, };

/**
 * What the backend knows of one object of one sample.
 */
export type InspectionWire = { "state": "Found", 
/**
 * At most 4096, and 8 MiB of paths and texts, with every text whole.
 */
fields: Array<FieldWire>, 
/**
 * Whether fields were left out.
 */
truncated: boolean, } | { "state": "Absent" } | { "state": "Unavailable" };

/**
 * One leaf of an object a search compares, at the key a search names it by.
 */
export type ItemWire = { 
/**
 * The lower-case group and the path, dotted: `debug.vendor.id`; a colour adds `rgb.r`, `rgb.g` and `rgb.b`.
 */
key: string, 
/**
 * The leaf.
 */
value: LeafWire, };

/**
 * The leaves of one object a search asked for.
 */
export type ObjectItemsWire = { 
/**
 * The object's id.
 */
entity: string, 
/**
 * Its leaves at the paths asked for; repeated keys keep every value.
 */
items: Array<ItemWire>, };
