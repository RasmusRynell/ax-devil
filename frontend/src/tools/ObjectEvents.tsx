import { useMemo } from "react";
import type { EventRowWire } from "../generated/api";
import { SectionCaption } from "../kit/SectionCaption";
import { itemOf, kindText, summaryOf, timeText, type EventItem } from "./eventItem";
import type { Column } from "./table/columns";
import { Table } from "./table/Table";

/** What an object page lists of an event: when, what and what it says beyond that. */
const COLUMNS: readonly Column<EventItem>[] = [
  { key: "time", title: "Time", kind: "mono", text: timeText, least: 12 },
  { key: "kind", title: "Kind", kind: "text", text: kindText, category: ({ kind }) => kind },
  { key: "event", title: "Event", kind: "text", text: summaryOf, stretch: true },
];

/**
 * The events that involve an object, in frame order, under the object's fields. A row opens the event's page.
 * `truncated` says the object has more events than the backend sent: these are the first ones.
 */
export function ObjectEvents({
  events,
  truncated,
  timeOf,
  onOpen,
}: {
  events: readonly EventRowWire[];
  truncated: boolean;
  timeOf: (frame: number) => number | undefined;
  onOpen: (event: EventRowWire) => void;
}) {
  const items = useMemo(() => events.map((row) => itemOf(row, timeOf)), [events, timeOf]);
  if (events.length === 0) return null;
  return (
    <section
      aria-label="Events"
      className="flex max-h-[calc(var(--spacing-row)*7)] shrink-0 flex-col border-t border-line"
    >
      <SectionCaption
        heading="Events"
        count={events.length}
        note={truncated ? "more not shown" : undefined}
      />
      <Table<EventItem>
        tableKey="object-events"
        label="Events"
        columns={COLUMNS}
        count={items.length}
        rowAt={(index) => items[index]}
        keyOf={(item) => String(item.id)}
        indexOf={(key) => items.findIndex((item) => String(item.id) === key)}
        selected={null}
        sort={{ key: "time", descending: false }}
        onSort={() => undefined}
        onActivate={(item) => {
          const row = events.find((event) => event.event === item.id);
          if (row) onOpen(row);
        }}
        cursorId={(item) => `row:object-event-${item.id}`}
      />
    </section>
  );
}
