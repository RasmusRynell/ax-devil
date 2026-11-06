import { useMemo, useSyncExternalStore, type ReactNode } from "react";
import { MiddleElided } from "../kit/MiddleElided";
import type { EventLog } from "../live/eventLog";
import type { PlaybackView } from "../playback/view";
import { matches, missingPaths, type SearchRecord } from "../search/match";
import { parseSearch } from "../search/query";
import { EVENT_COLUMNS, involved, positionOf, type EventItem } from "./eventItem";
import { rowsUpTo } from "./eventLine";
import type { EventsState } from "./listState";
import { neighbour, type Steps } from "./pageStack";
import { EVENT_SYNTAX, SearchBar } from "./SearchBar";
import { Table } from "./table/Table";
import { NO_STEPS, UnderPage, type Open } from "./UnderPage";
import { useApplied } from "./useApplied";

/** What a search looks at in an event: its kind and the objects it involves as fields, its label as text. */
const recordOf = (event: EventItem): SearchRecord => ({
  fields: { kind: [event.kind], object: involved(event) },
  text: [event.label],
});

/**
 * The Events list of a live stream: the newest events as their overlays were shown, oldest first, searched here. The
 * stream has no history to ask, so the list is the log the viewer keeps whether or not this shows. The events of the
 * frame on screen are tinted, and the list follows its end until the user scrolls away from it, and follows again once
 * they bring it back. A marker lies where the overlay's decoder fell behind and samples were lost.
 */
export function LiveEventsList({
  log,
  view,
  state,
  onState,
  mark,
  open,
  onOpen,
  header,
}: {
  log: EventLog;
  view: PlaybackView;
  /** The search and the follow choice, which the viewer keeps while the panel is closed. */
  state: EventsState;
  onState: (state: EventsState) => void;
  /** The event whose row is marked: the one a page was last opened from. */
  mark: number | null;
  open: Open | null;
  onOpen: (event: EventItem) => void;
  header: ReactNode;
}) {
  const { text, following, memory } = state;
  const { entries, dropped, generation } = useSyncExternalStore(log.subscribe, log.getSnapshot);
  const [applied, apply] = useApplied(text);
  const { wire } = useMemo(() => parseSearch(applied), [applied]);
  const filtered = wire.groups.length > 0;
  const events = useMemo(() => entries.filter((entry) => entry.kind !== "gap"), [entries]);
  const rows = useMemo(
    () => (filtered ? events.filter((event) => matches(wire, recordOf(event))) : entries),
    [filtered, wire, events, entries],
  );
  const at = useMemo(() => new Map(rows.map(({ id }, index) => [id, index])), [rows]);
  const missing = useMemo(() => missingPaths(wire, events.map(recordOf)), [wire, events]);
  // The frame on screen is of the stream's newest run of frame numbers; the events of earlier runs are past.
  const shown =
    view.shown === undefined ? undefined : positionOf(generation, view.shown.sequenceId);
  const line = useMemo(
    () =>
      shown === undefined
        ? null
        : rowsUpTo(
            rows.map((row) => positionOf(row.generation, row.frame)),
            shown,
          ),
    [rows, shown],
  );

  const from = open?.from ?? null;
  const steps = useMemo<Steps>(() => {
    if (from === null) return NO_STEPS;
    const openable = rows.filter((row) => row.kind !== "gap");
    const keys = openable.map((row) => String(row.id));
    const page = (delta: number) => {
      const key = neighbour({ keys, offset: 0, matched: keys.length }, from, delta);
      const event = key === null ? undefined : openable[keys.indexOf(key)];
      return event ? ({ kind: "event", event } as const) : null;
    };
    return { previous: page(-1), next: page(1) };
  }, [from, rows]);

  const message =
    rows.length === 0 ? (
      <p role="status" className="m-0 pt-l text-center text-small text-muted">
        {entries.length === 0 ? "No events" : "No matching events"}
      </p>
    ) : undefined;

  return (
    <UnderPage open={open} steps={steps}>
      {header}
      <div className="p-m pb-s">
        <SearchBar
          label="Search events"
          syntax={EVENT_SYNTAX}
          value={text}
          onChange={(value) => onState({ ...state, text: value })}
          onCommit={(value) => {
            apply(value);
            onState({ ...state, text: value });
          }}
        />
        {filtered && (
          <div
            role="status"
            className="pt-xs text-small text-muted"
          >{`${rows.length} of ${events.length}`}</div>
        )}
        {missing.length > 0 && (
          <div role="status" className="pt-xs text-small text-muted">
            <MiddleElided text={`No event has ${missing.join(", ")}`} />
          </div>
        )}
        {dropped > 0 && (
          <div
            role="status"
            className="pt-xs text-small text-muted"
          >{`${dropped} older dropped`}</div>
        )}
      </div>
      <Table<EventItem>
        tableKey="events"
        label="Events"
        columns={EVENT_COLUMNS}
        count={rows.length}
        rowAt={(index) => rows[index]}
        keyOf={(event) => String(event.id)}
        indexOf={(key) => at.get(Number(key)) ?? -1}
        selected={mark === null ? null : String(mark)}
        sort={{ key: "time", descending: false }}
        onSort={() => undefined}
        dimmed={(event) =>
          event.kind === "gap" ||
          (shown !== undefined && positionOf(event.generation, event.frame) > shown)
        }
        tint={(event) =>
          event.kind !== "gap" && positionOf(event.generation, event.frame) === shown
        }
        follow={{
          line,
          keep: "end",
          following,
          onFollowing: (on) => onState({ ...state, following: on }),
        }}
        memory={memory}
        message={message}
        onActivate={(event) => event.kind !== "gap" && onOpen(event)}
        cursorId={(event) => `row:event-${event.id}`}
      />
    </UnderPage>
  );
}
