// Generated from api_types::playback::file_scope by make; do not edit.

import type { ErrorWire } from "./api_types.error";
import type { EventDetailWire, EventRowWire } from "./scene_store.events";
import type { FileRowWire } from "./scene_store.query";

/**
 * The answer to a File scope request (`FileQuery`, `Appearances`, `EventQuery` or `Event`): what the file holds, or
 * why it cannot be asked. The client matches it to its request by `ServerMessage::File`'s `lane` and `request`.
 */
export type FileAnswer = { "state": "Found", 
/**
 * The source revision the answer was made under; the client drops an answer of any other.
 */
data_revision: number, 
/**
 * The lane policy revision the answer was placed under; the client drops an answer of any other.
 */
policy_revision: number, 
/**
 * What was found; its kind is the request's.
 */
body: FileBody, } | { "state": "Unavailable", 
/**
 * Why the file cannot be asked.
 */
unavailable: FileScopeUnavailable, };

/**
 * What a found File scope answer holds, by the kind of request it answers.
 */
export type FileBody = { "kind": "Objects", 
/**
 * How many objects the video shows that the class toggles keep.
 */
total: number, 
/**
 * How many of them the search matches, which is how many rows there are.
 */
matched: number, 
/**
 * The request's `offset`.
 */
offset: number, 
/**
 * Rows `offset` to `offset + rows.len()` of the list.
 */
rows: Array<FileRowWire>, 
/**
 * Where the request's `locate` object is in the list; `None` when the request named none.
 */
located: Located | null, } | { "kind": "Appearances", 
/**
 * The object's id from the request.
 */
entity: string, 
/**
 * The frames the video shows the object on, as `[first, last]` spans of consecutive frames, ascending, with a
 * frame between two. Empty when the file has no such object or no frame shows it.
 */
spans: Array<[number, number]>, 
/**
 * Whether the object has more spans than are sent: these are its first ones.
 */
truncated: boolean, 
/**
 * The events that involve the object, in frame order.
 */
events: Array<EventRowWire>, 
/**
 * Whether the object has more events than are sent: these are its first ones.
 */
events_truncated: boolean, } | { "kind": "Events", 
/**
 * How many events the video shows.
 */
total: number, 
/**
 * How many of them the search matches, which is how many rows there are.
 */
matched: number, 
/**
 * The request's `offset`.
 */
offset: number, 
/**
 * Rows `offset` to `offset + rows.len()` of the list, in frame order.
 */
rows: Array<EventRowWire>, 
/**
 * The row of the request's `locate` event; `None` when the request named none or the list does not hold it.
 */
located: number | null, 
/**
 * How many rows are on frames at or before the request's `frame`, which is where the line for that frame
 * goes; `None` when the request named none.
 */
frame_row: number | null, } | { "kind": "Event", 
/**
 * The event from the request.
 */
event: number, 
/**
 * The event; `None` when the file has no such event or the video does not show it.
 */
detail: EventDetailWire | null, };

/**
 * Why a File scope query has no rows.
 */
export type FileScopeUnavailable = { "state": "Reading" } | { "state": "Following" } | { "state": "TooLarge" } | { "state": "Failed" } & ErrorWire | { "state": "PlainLane" };

/**
 * Where an object is in a File scope list, the answer to `ClientMessage::FileQuery`'s `locate`.
 */
export type Located = { 
/**
 * The object's id from the request.
 */
id: string, 
/**
 * The index of its row in the whole list, whichever page was asked; `None` when the list does not hold it.
 */
row: number | null, };
