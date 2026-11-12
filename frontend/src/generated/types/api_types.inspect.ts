// Generated from api_types::inspect by make; do not edit.

import type { FieldGroupWire, FieldOriginWire } from "./scene.inspection";
import type { LeafWire } from "./scene.inspection.leaf";

/**
 * One leaf of an object's data.
 */
export type FieldWire = { 
/**
 * Stable digest of the full segmented path, only when its display path is abbreviated.
 */
identity?: string, 
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
 * Ordinal and full UTF-8 size when only a text preview is included.
 */
cut?: [number, number], 
/**
 * Ordinal and full dotted path size when the path exceeds a page.
 */
path_cut?: [number, number], 
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
 * A capability valid only in the socket that issued it, for one held entity/sample revision.
 */
export type InspectionContinuation = { 
/**
 * Opaque capability; never grants authority beyond the requesting socket.
 */
token: string, 
/**
 * Next field ordinal, absent on the final page.
 */
next: number | null, };

/**
 * One page or text chunk requested through the playback socket.
 */
export type InspectionRead = { 
/**
 * Correlation number owned by the client's inspection ordering.
 */
request: number, 
/**
 * Capability from the initial inspection.
 */
token: string, 
/**
 * Field ordinal for text retrieval; absent for a page.
 */
field: number | null, 
/**
 * Retrieve the complete Raw section as JSON, independently of the current page.
 */
raw?: boolean, 
/**
 * Retrieve the dotted path instead of the value.
 */
path: boolean, 
/**
 * Field ordinal for a page, UTF-8 byte offset for a text chunk.
 */
offset: number, };

/**
 * The result of one bounded continuation request.
 */
export type InspectionReadResult = { "state": "Page", 
/**
 * Bounded fields in stable order.
 */
fields: Array<FieldWire>, 
/**
 * Next page ordinal, absent at the end.
 */
next: number | null, } | { "state": "Chunk", 
/**
 * At most 64 KiB of UTF-8 text.
 */
text: string, 
/**
 * Full UTF-8 byte count.
 */
total: number, 
/**
 * Next byte offset, absent at the end.
 */
next: number | null, } | { "state": "Refused", 
/**
 * Safe reason shown in the inspector.
 */
reason: string, };

/**
 * What the backend knows of one object of one sample.
 */
export type InspectionWire = { "state": "Found", 
/**
 * At most 4096 fields and 8 MiB of paths/text; large texts carry a preview and retrieval metadata.
 */
fields: Array<FieldWire>, 
/**
 * Socket-local continuation capability and the next page's ordinal.
 */
continuation?: InspectionContinuation, 
/**
 * Whether fields were left out.
 */
truncated: boolean, } | { "state": "Refused", 
/**
 * Reason shown by the Inspector.
 */
reason: string, } | { "state": "Absent" } | { "state": "Unavailable" };

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

/**
 *The maximum UTF-8 bytes assembled for one explicit inspection copy.
 */
export const MAX_COPY_BYTES = 268435456;
