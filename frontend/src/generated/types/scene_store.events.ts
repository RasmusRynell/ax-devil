// Generated from scene_store::events by make; do not edit.

import type { OperationKindWire } from "./scene.scene";

/**
 * One event with the objects it involves and the events of its moment.
 */
export type EventDetailWire = { 
/**
 * The event.
 */
row: EventRowWire, 
/**
 * The objects it involves, each once, in the order the event names them.
 */
objects: Array<InvolvedWire>, 
/**
 * The first other events on its frame, in index order.
 */
same_moment: Array<EventRowWire>, 
/**
 * Whether its frame has more other events than `same_moment` holds.
 */
same_moment_truncated: boolean, };

/**
 * An object an event involves, as a chip of the list shows it.
 */
export type EventObjectWire = { 
/**
 * The entity id.
 */
id: string, 
/**
 * The first class the object was recorded with, which colors its chip; `None` when the video shows the object
 * nowhere, and the chip takes the event's kind.
 */
class: string | null, };

/**
 * An event of the file as a list shows it.
 */
export type EventRowWire = { 
/**
 * Bounded identity for finding this event after a file replacement.
 */
anchor: [number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number, number], 
/**
 * The event's index in the file's events, which names it in a request.
 */
event: number, 
/**
 * The frame the video shows it on.
 */
frame: number, 
/**
 * What the event does.
 */
kind: OperationKindWire, 
/**
 * The event as one line of text.
 */
label: string, 
/**
 * The objects it involves, in order and with repeats.
 */
objects: Array<EventObjectWire>, };

/**
 * An object an event involves, with where the video shows it.
 */
export type InvolvedWire = { 
/**
 * The entity id.
 */
id: string, 
/**
 * The classes it was recorded with, in order of first appearance; none when the video shows it nowhere.
 */
classes: Array<string>, 
/**
 * The first and last frame the video shows it on; `None` when it shows it nowhere, such as an id only the event
 * names.
 */
seen: [number, number] | null, 
/**
 * Where it is on screen as `[first, last]` frames, in order, merged so a strip of `bins` marks can draw it.
 */
presence: Array<[number, number]>, };
