// Generated from api_types::playback::events by make; do not edit.

import type { EventRowWire } from "./scene_store.events";
import type { SearchWire } from "./scene_store.query";

/**
 * Asks for one event of a lane's file with its objects and the events of its frame. The answer is `File`, with a `FileBody::Event`.
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
 * The answer is `File`, with a `FileBody::Events`. A `limit` outside 1 to 200 is a `LaneProblem`.
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
 * Finds the nearest event equal in kind, label and ordered entity ids after a file replacement.
 * Uses the same File scope authority and answer as `Event`; `event` echoes the prior index.
 */
export type FindEventWire = { 
/**
 * The lane's position.
 */
lane: number, 
/**
 * The request id.
 */
request: number, 
/**
 * The previously shown event and frame.
 */
previous: EventRowWire, 
/**
 * At most 256 presence bins per object.
 */
bins: number, };
