/** The Events list of a live stream: the recent changes to object identity, oldest first, within a fixed size. */

import type { LiveEventsWire } from "../generated/api";
import type { EventItem } from "../tools/eventItem";

/** How many entries the list keeps; older ones are dropped and counted. */
export const EVENT_LOG_CAP = 1000;

/** What the Events list shows: its entries oldest first, how many of them are events and how many entries fell off its start. */
export type EventsView = { entries: readonly EventItem[]; events: number; dropped: number };

/**
 * The list a live viewer records into whether or not the media tools are showing it. It has no history to seek: what
 * passes the cap is gone, and `dropped` says how much. An entry is an event of a frame, or `gap` where samples were
 * lost, which lies on the frame of the event before it.
 */
export class EventLog {
  readonly #listeners = new Set<() => void>();
  #view: EventsView = { entries: [], events: 0, dropped: 0 };
  #next = 0;
  #frame = 0;

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  getSnapshot = (): EventsView => this.#view;

  /** Records the events a released frame made visible, sample by sample in the order sent. */
  addEvents({ frame, samples }: Pick<LiveEventsWire, "frame" | "samples">): void {
    this.#frame = frame;
    const added: EventItem[] = samples.flatMap(({ time_us, events }) =>
      events.map(({ kind, label, entities }) => ({
        id: this.#next++,
        frame,
        timeUs: time_us,
        kind,
        label,
        objects: entities.map((id) => ({ id, class: null })),
      })),
    );
    this.#append(added);
  }

  /** Records that samples were lost from `timeUs` on. */
  addGap(timeUs: number): void {
    this.#append([
      {
        id: this.#next++,
        frame: this.#frame,
        timeUs,
        kind: "gap",
        label: "Samples lost",
        objects: [],
      },
    ]);
  }

  #append(added: readonly EventItem[]): void {
    if (added.length === 0) return;
    const all = [...this.#view.entries, ...added];
    const cut = Math.max(0, all.length - EVENT_LOG_CAP);
    const entries = cut === 0 ? all : all.slice(cut);
    const events = entries.filter(({ kind }) => kind !== "gap").length;
    this.#view = { entries, events, dropped: this.#view.dropped + cut };
    this.#listeners.forEach((listener) => listener());
  }
}
