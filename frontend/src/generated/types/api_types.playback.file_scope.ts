// Generated from api_types::playback::file_scope by make; do not edit.

import type { ErrorWire } from "./api_types.error";
import type { EventRowWire } from "./scene_store.events";

/**
 * Where one object is on the video, the answer to `ClientMessage::Appearances`.
 */
export type FileAppearances = { "state": "Found", 
/**
 * The source revision they were made under; the client drops spans of any other.
 */
data_revision: number, 
/**
 * The lane policy revision they were placed under; the client drops spans of any other.
 */
policy_revision: number, 
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
events_truncated: boolean, } | { "state": "Unavailable", 
/**
 * Why the file cannot be asked.
 */
unavailable: FileScopeUnavailable, };

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
