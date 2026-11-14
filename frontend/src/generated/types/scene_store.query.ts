// Generated from scene_store::query by make; do not edit.

import type { OperationKindWire } from "./scene.scene";

/**
 * An object of the file as a list shows it.
 */
export type FileRowWire = { 
/**
 * The entity id.
 */
id: string, 
/**
 * The classes it was recorded with, in order of first appearance.
 */
classes: Array<string>, 
/**
 * The kinds of event it is in, in the order delete, rename, merge, split.
 */
events: Array<OperationKindWire>, 
/**
 * The first frame the video shows it on.
 */
first_frame: number, 
/**
 * The last frame the video shows it on.
 */
last_frame: number, 
/**
 * Where it is on screen as `[first, last]` frames, in order, merged so a strip of `bins` marks can draw it; at
 * most as many as the request's `bins`.
 */
presence: Array<[number, number]>, };

/**
 * What the rows are sorted by.
 */
export type FileSortKey = "id" | "class" | "first_seen";

/**
 * The order of the rows. Rows equal in the key are in ascending id order whichever way the key goes.
 */
export type FileSortWire = { 
/**
 * What to sort by.
 */
key: FileSortKey, 
/**
 * Whether the key runs from greatest to least.
 */
descending: boolean, };

/**
 * How a term compares a field with its value.
 */
export type SearchOp = "eq" | "gt" | "lt" | "ge" | "le";

/**
 * A parsed search: every group must match, and a group matches when any of its terms does. No group matches
 * everything.
 */
export type SearchWire = { 
/**
 * The groups, ANDed.
 */
groups: Array<Array<TermWire>>, };

/**
 * One condition of a search.
 */
export type TermWire = { 
/**
 * The field, or `null` to search the free text. A path names a field when it equals it or its trailing dotted
 * parts, ignoring case; one that names none of an object's fields matches nothing.
 */
path: string | null, 
/**
 * How the field is compared with `value`.
 */
op: SearchOp, 
/**
 * What to compare with, or the text to look for when there is no path.
 */
value: string, 
/**
 * Whether the term holds where it does not match.
 */
negated: boolean, };
