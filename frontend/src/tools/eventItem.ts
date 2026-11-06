/** An event as the media tools show it, whether the file lists it or a live stream reported it. */

import type { EventObjectWire, EventRowWire, OperationKindWire } from "../generated/api";
import type { Column } from "./table/columns";

/** One line of an Events list. */
export type EventItem = {
  /** Names it in its list: its index in the file's events, or the live list's own count. */
  id: number;
  /** The video frame it is on. */
  frame: number;
  /** The frame's time in microseconds; `undefined` until the video is described. */
  timeUs: number | undefined;
  /** What the event does; `gap` marks where a live stream lost samples. */
  kind: OperationKindWire | "gap";
  /** The event as one line of text. */
  label: string;
  /** The objects it involves, in order and with repeats. */
  objects: readonly EventObjectWire[];
};

/** What an event of the file is as a line of a list. */
export const itemOf = (
  row: EventRowWire,
  timeOf: (frame: number) => number | undefined,
): EventItem => ({
  id: row.event,
  frame: row.frame,
  timeUs: timeOf(row.frame),
  kind: row.kind,
  label: row.label,
  objects: row.objects,
});

/** A time as `HH:MM:SS.mmm`, the clock of its UTC day (the video position for a recording). */
export function clockOf(us: number): string {
  const part = (value: number, width: number): string => String(value).padStart(width, "0");
  const hours = Math.floor(us / 3_600_000_000) % 24;
  const minutes = Math.floor(us / 60_000_000) % 60;
  const seconds = Math.floor(us / 1_000_000) % 60;
  return `${part(hours, 2)}:${part(minutes, 2)}:${part(seconds, 2)}.${part(Math.floor((us % 1_000_000) / 1000), 3)}`;
}

/** What the event's label says beyond its kind, such as `12 → 17` for a merge, or the whole label. */
export function summaryOf({ kind, label }: Pick<EventItem, "kind" | "label">): string {
  const rest = label.toLowerCase().startsWith(kind) ? label.slice(kind.length).trim() : label;
  return rest || label;
}

/** What the Kind column and the event page's tag say. */
export const kindText = ({ kind }: Pick<EventItem, "kind">): string =>
  kind === "gap" ? "samples lost" : kind;

/** The ids of the objects an event involves, once each, in the order it names them. */
export const involved = (item: Pick<EventItem, "objects">): string[] => [
  ...new Set(item.objects.map(({ id }) => id)),
];

/** The time of a line, or nothing while the video is not described. */
export const timeText = ({ timeUs }: Pick<EventItem, "timeUs">): string =>
  timeUs === undefined ? "" : clockOf(timeUs);

/** What an Events list shows of an event: when, what and which objects; the time is never cut. */
export const EVENT_COLUMNS: readonly Column<EventItem>[] = [
  { key: "time", title: "Time", kind: "mono", text: timeText, least: 12 },
  { key: "kind", title: "Kind", kind: "text", text: kindText, category: ({ kind }) => kind },
  {
    key: "objects",
    title: "Objects",
    kind: "text",
    text: (item) => item.objects.map(({ id }) => id).join(", "),
    muted: true,
    stretch: true,
  },
];
