// Generated from api_types::playback::events by make; do not edit.

import type { FileScopeUnavailable } from "./api_types.playback.file_scope";
import type { EventDetailWire, EventRowWire } from "./scene_store.events";
import type { SearchWire } from "./scene_store.query";

/**
 * Asks for one event of a lane's file with its objects and the events of its frame. The answer is `Event`.
 */
export type EventAskWire = { 
/**
 * The lane's position in `Opened.lanes`.
 */
lane: number, 
/**
 * Names the request in its answer; the client drops answers of older requests.
 */
request: number, 
/**
 * The event's index in the file's events: `EventRow::event`.
 */
event: number, 
/**
 * The most spans of presence per object, from 0 to 256; 0 sends none.
 */
bins: number, };

/**
 * Asks for rows of a lane's event list: every event the video shows, in frame order, kept by the search and paged.
 * The answer is `EventRows`. A `limit` outside 1 to 200 is a `LaneProblem`.
 */
export type EventQueryWire = { 
/**
 * The lane's position in `Opened.lanes`.
 */
lane: number, 
/**
 * Names the request in its answer; the client drops answers of older requests.
 */
request: number, 
/**
 * The search, parsed by the client: fields `kind` and `object`, free text over the label.
 */
query: SearchWire, 
/**
 * The first row to send.
 */
offset: number, 
/**
 * How many rows to send at most, from 1 to 200.
 */
limit: number, 
/**
 * An event whose row in the list is wanted whatever the page: the answer's `located`.
 */
locate: number | null, 
/**
 * A frame whose place in the list is wanted: the answer's `frame_row`.
 */
frame: number | null, };

/**
 * The answer to `EventQuery`.
 */
export type EventRowsWire = { 
/**
 * The lane's position in `Opened.lanes`.
 */
lane: number, 
/**
 * The request answered.
 */
request: number, 
/**
 * The rows, or why there are none.
 */
rows: FileEvents, };

/**
 * The answer to `Event`.
 */
export type EventWire = { 
/**
 * The lane's position in `Opened.lanes`.
 */
lane: number, 
/**
 * The request answered.
 */
request: number, 
/**
 * The event from the request.
 */
event: number, 
/**
 * The event, or why there is none.
 */
detail: FileEvent, };

/**
 * One event of a lane's file, or why there is none.
 */
export type FileEvent = { "state": "Found", 
/**
 * The source revision it was made under; the client drops an answer of any other.
 */
data_revision: number, 
/**
 * The lane policy revision the events were placed under; the client drops an answer of any other.
 */
policy_revision: number, 
/**
 * The event; `None` when the file has no such event or the video does not show it.
 */
detail: EventDetailWire | null, } | { "state": "Unavailable", 
/**
 * Why the file cannot be asked.
 */
unavailable: FileScopeUnavailable, };

/**
 * Rows of a lane's event list, or why there are none.
 */
export type FileEvents = { "state": "Found", 
/**
 * The source revision it was listed under; the client drops rows of any other.
 */
data_revision: number, 
/**
 * The lane policy revision the events were placed under; the client drops rows of any other.
 */
policy_revision: number, 
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
frame_row: number | null, } | { "state": "Unavailable", 
/**
 * Why the file cannot be asked.
 */
unavailable: FileScopeUnavailable, };
