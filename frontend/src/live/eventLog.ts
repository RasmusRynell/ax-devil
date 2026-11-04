/** The Events list of a live stream: the recent changes to object identity, newest last, within a fixed size. */

import type { LiveEventsWire, OperationKindWire } from "../generated/api";

/** How many entries the list keeps; older ones are dropped and counted. */
export const EVENT_LOG_CAP = 1000;

/** One line of the list: an event, or the marker of where samples were lost. */
export type LogEntry =
  | {
      id: number;
      type: "event";
      /** The event's sample time on the video clock in microseconds. */
      timeUs: number;
      op: OperationKindWire;
      label: string;
      entities: readonly string[];
    }
  | { id: number; type: "gap"; timeUs: number };

/** What the Events list shows: its entries oldest first, and how many entries fell off its start. */
export type EventsView = { entries: readonly LogEntry[]; dropped: number };

/**
 * The list a live viewer records into whether or not the Events panel is showing. It has no history to seek: what
 * passes the cap is gone, and `dropped` says how much.
 */
export class EventLog {
  readonly #listeners = new Set<() => void>();
  #view: EventsView = { entries: [], dropped: 0 };
  #next = 0;

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  };

  getSnapshot = (): EventsView => this.#view;

  /** Records the events a released frame made visible, sample by sample in the order sent. */
  addEvents({ samples }: Pick<LiveEventsWire, "samples">): void {
    const added: LogEntry[] = samples.flatMap(({ time_us, events }) =>
      events.map(({ kind, label, entities }) => ({
        id: this.#next++,
        type: "event" as const,
        timeUs: time_us,
        op: kind,
        label,
        entities,
      })),
    );
    this.#append(added);
  }

  /** Records that samples were lost from `timeUs` on. */
  addGap(timeUs: number): void {
    this.#append([{ id: this.#next++, type: "gap", timeUs }]);
  }

  #append(added: readonly LogEntry[]): void {
    if (added.length === 0) return;
    const all = [...this.#view.entries, ...added];
    const cut = Math.max(0, all.length - EVENT_LOG_CAP);
    this.#view = { entries: cut === 0 ? all : all.slice(cut), dropped: this.#view.dropped + cut };
    this.#listeners.forEach((listener) => listener());
  }
}
