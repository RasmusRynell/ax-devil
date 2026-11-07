import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { Button } from "../kit/Button";
import { MiddleElided } from "../kit/MiddleElided";
import type { PlaybackView } from "../playback/view";
import { missingPaths, type SearchRecord } from "../search/match";
import { parseSearch } from "../search/query";
import { EVENT_COLUMNS, itemOf, type EventItem } from "./eventItem";
import { eventRow, lineFor, lineOf } from "./eventLine";
import type { EventsState } from "./listState";
import { staleOf, whyUnavailable } from "./listStatus";
import type { ObjectSource } from "./ObjectsList";
import { neighbour, type Steps } from "./pageStack";
import { EVENT_SYNTAX, SearchBar } from "./SearchBar";
import { Table } from "./table/Table";
import { NO_STEPS, UnderPage, type Open } from "./UnderPage";

/** Rows asked of the backend at once, and how many rows the window moves before another page is asked for. */
const PAGE = 200;
const STEP = 50;

/** The fields an event can be searched by; a path no event has is named under the search box. */
const RECORD: SearchRecord = { fields: { kind: [], object: [] }, text: [] };

/**
 * The Events list of one lane's file: every event the video shows, in frame order, as the backend lists them a page at
 * a time. The events of the frame on screen are tinted, those still to come dimmed, and a line lies between what has
 * happened and what is to come; the list scrolls to keep the line in view until the user scrolls away from it, and
 * follows again once they bring it back. While a page is `open` the list stays under it, as the objects list does.
 */
export function EventsList({
  source,
  view,
  lane,
  state,
  onState,
  mark,
  open,
  onOpen,
  header,
}: {
  source: ObjectSource;
  view: PlaybackView;
  lane: number;
  /** The search and the follow choice, which the viewer keeps while the panel is closed. */
  state: EventsState;
  onState: (state: EventsState) => void;
  /** The event whose row is marked: the one a page was last opened from. */
  mark: number | null;
  open: Open | null;
  onOpen: (event: EventItem) => void;
  header: ReactNode;
}) {
  const { text, following, anchor, memory } = state;
  const events = source.files.events;
  const [offset, setOffset] = useState(0);
  const [committed, setCommitted] = useState(false);
  const searchBar = useRef<{ focus(): void }>(null);
  const { wire } = useMemo(() => parseSearch(text), [text]);
  const listed = useSyncExternalStore(source.files.subscribe, () => events.view(lane));
  const { page, unavailable } = listed;
  const frame = view.shown?.sequenceId;
  const items = useMemo(
    () => page?.rows.map((row) => itemOf(row, (at) => source.frameTime(at))) ?? [],
    // The frames' times are known once the video is described, which `frameCount` follows.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [page, source, view.frameCount],
  );

  // The rows around an open page's event are the ones asked for, so that the page can step to its neighbours.
  const from = open?.from ?? null;
  const eventId = from === null ? null : Number(from);
  const { row, near } = eventRow(eventId, page, anchor);
  const wanted = near === null ? offset : Math.max(0, Math.floor(near / STEP) * STEP - STEP);
  useEffect(() => {
    if (eventId !== null && row !== null && (anchor?.event !== eventId || anchor.row !== row))
      onState({ ...state, anchor: { event: eventId, row } });
  }, [eventId, row, anchor, state, onState]);

  // The backend is asked where the frame is only when the rows loaded do not say.
  const asked = lineOf(page, frame) === null && frame !== undefined ? frame : null;
  useEffect(() => {
    events.request(lane, text, wanted, PAGE, eventId, asked);
  }, [events, lane, text, wanted, eventId, asked]);
  // Return and Clear ask for the list at once; this runs after the request the committed text made.
  useEffect(() => {
    if (!committed) return;
    events.flush(lane);
    setCommitted(false);
  }, [events, lane, committed]);

  // A replaced file is a new list, which follows the line again.
  const revision = useRef(page?.revision);
  useEffect(() => {
    const changed =
      page !== null && revision.current !== undefined && revision.current !== page.revision;
    if (page) revision.current = page.revision;
    if (changed && !state.following) onState({ ...state, following: true });
  }, [page, state, onState]);

  const onRange = useCallback((start: number) => setOffset(Math.floor(start / STEP) * STEP), []);
  const steps = useMemo<Steps>(() => {
    if (from === null || !page) return NO_STEPS;
    const rows = {
      keys: page.rows.map((event) => String(event.event)),
      offset: page.offset,
      matched: page.matched,
    };
    const at = (delta: number) => {
      const key = neighbour(rows, from, delta);
      const event = key === null ? undefined : items[rows.keys.indexOf(key)];
      return event ? ({ kind: "event", event } as const) : null;
    };
    return { previous: at(-1), next: at(1) };
  }, [from, page, items]);

  const total = page?.total ?? 0;
  const matched = page?.matched ?? 0;
  const missing = total > 0 ? missingPaths(wire, [RECORD]) : [];
  const stale = staleOf(listed);
  const filtered = wire.groups.length > 0;
  const message = page ? (
    matched === 0 ? (
      <p role="status" className="m-0 pt-l text-center text-small text-muted">
        {total === 0 ? "No events" : "No matching events"}
      </p>
    ) : undefined
  ) : (
    <p
      role="status"
      aria-busy={unavailable === null || unavailable.state === "Reading"}
      className="m-0 p-m text-muted"
    >
      {unavailable ? whyUnavailable(unavailable, "This lane has no events") : "Loading…"}
    </p>
  );
  const line = lineFor(page, frame);

  return (
    <UnderPage open={open} steps={steps}>
      {header}
      <div className="p-m pb-s">
        <SearchBar
          handle={searchBar}
          label="Search events"
          syntax={EVENT_SYNTAX}
          value={text}
          onChange={(value) => onState({ ...state, text: value })}
          onCommit={(value) => {
            onState({ ...state, text: value });
            setCommitted(true);
          }}
        />
        {filtered && page && (
          <div
            role="status"
            className="pt-xs text-small text-muted"
          >{`${matched} of ${total}`}</div>
        )}
        {missing.length > 0 && (
          <div role="status" className="pt-xs text-small text-muted">
            <MiddleElided text={`No event has ${missing.join(", ")}`} />
          </div>
        )}
        {stale !== null && (
          <div role="status" className="flex items-center gap-s pt-xs text-small text-muted">
            <span className="min-w-0 flex-1">{stale}</span>
            <Button
              size="small"
              onClick={() => {
                events.flush(lane);
                // The button goes when the rows arrive; focus is not left on it to be lost then.
                searchBar.current?.focus();
              }}
            >
              Retry
            </Button>
          </div>
        )}
      </div>
      <Table<EventItem>
        key="events"
        tableKey="events"
        label="Events"
        columns={EVENT_COLUMNS}
        count={matched}
        rowAt={(index) => (page ? items[index - page.offset] : undefined)}
        keyOf={(event) => String(event.id)}
        indexOf={(key) => eventRow(Number(key), page, null).row ?? -1}
        selected={mark === null ? null : String(mark)}
        sort={{ key: "time", descending: false }}
        onSort={() => undefined}
        dimmed={(event) => frame !== undefined && event.frame > frame}
        tint={(event) => event.frame === frame}
        follow={{
          line,
          keep: "third",
          following,
          onFollowing: (on) => onState({ ...state, following: on }),
        }}
        memory={memory}
        busy={listed.pending}
        message={message}
        onActivate={onOpen}
        onRange={onRange}
        cursorId={(event) => `row:event-${event.id}`}
      />
    </UnderPage>
  );
}
