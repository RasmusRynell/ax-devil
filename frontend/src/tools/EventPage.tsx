import { useMemo, useState, type ReactNode } from "react";
import type { InvolvedWire } from "../generated/api";
import { Button } from "../kit/Button";
import { IconButton } from "../kit/IconButton";
import { SectionCaption } from "../kit/SectionCaption";
import { useCopied } from "../inspect/useCopied";
import { categoryDot } from "./category";
import { clockOf, kindText, summaryOf, type EventItem } from "./eventItem";
import { shortTime } from "./objectColumns";
import { UNKNOWN_CLASS } from "./objects";
import { PresenceStrip } from "./PresenceStrip";
import { nextSort, type Column, type SortOf } from "./table/columns";
import { Table } from "./table/Table";

/** What the event page knows of an event beyond the line it was opened from. */
export type EventDetail = {
  /** The objects it involves, once each, in the order it names them. */
  objects: readonly InvolvedWire[];
  /** The other events of its moment, which a live stream has no history to find. */
  sameMoment: readonly EventItem[];
  /** The moment has more other events than `sameMoment` lists. */
  truncated: boolean;
};

type ObjectSort = "" | "id" | "class" | "seen";

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

const classOf = (row: InvolvedWire): string => row.classes.join(", ");

/** The rows in the order `sort` asks for; the sort is stable, so equal rows keep the order the event names them. */
function sorted(rows: readonly InvolvedWire[], { key, descending }: SortOf<ObjectSort>) {
  if (key === "") return rows;
  const order = (a: InvolvedWire, b: InvolvedWire): number =>
    key === "id"
      ? collator.compare(a.id, b.id)
      : key === "class"
        ? collator.compare(classOf(a), classOf(b))
        : (a.seen?.[0] ?? -1) - (b.seen?.[0] ?? -1);
  return [...rows].sort((a, b) => (descending ? -order(a, b) : order(a, b)));
}

/** A time to the ms, or nothing while the video is not described. */
const SEEN = (first: number, last: number, timeOf: (frame: number) => number | undefined) => {
  const from = timeOf(first);
  const to = timeOf(last);
  return from === undefined || to === undefined ? "" : `${shortTime(from)}–${shortTime(to)}`;
};

const MOMENT_COLUMNS: readonly Column<EventItem>[] = [
  {
    key: "kind",
    title: "Kind",
    kind: "text",
    text: kindText,
    category: ({ kind }) => kind,
    least: 9,
  },
  { key: "event", title: "Event", kind: "text", text: summaryOf, stretch: true },
];

/** One fixed field of the event: a muted label and its value. */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-h-row items-center gap-m px-m">
      <dt className="w-[8ch] shrink-0 text-small text-muted">{label}</dt>
      <dd className="m-0 flex min-w-0 flex-1 items-center gap-s">{children}</dd>
    </div>
  );
}

/**
 * The body of an event's page: what happened and when, the objects it involves, and the other events of its moment.
 * Unless `live`, Go to frame seeks to the event's frame, the objects show where they appear, and the other events of
 * the moment are listed; a live stream has no history for any of these. An object dimmed is one the frame on screen
 * lacks (`present` says which are there). The page's header is the panel's.
 */
export function EventPage({
  item,
  detail,
  message,
  live,
  frames,
  current,
  timeOf,
  present,
  onGo,
  onObject,
  onEvent,
}: {
  item: EventItem;
  /** What the backend knows of the event, empty while it does not. */
  detail: EventDetail;
  /** Said while there is nothing to list: loading, or why there is none. */
  message?: ReactNode;
  /** The event is of a live stream, which has no frames to go to. */
  live: boolean;
  /** How many frames the video has. */
  frames: number;
  /** The frame on screen. */
  current: number | undefined;
  timeOf: (frame: number) => number | undefined;
  /** The ids of the objects the frame on screen has; `null` while no frame is on screen. */
  present: ReadonlySet<string> | null;
  /** Goes to the event's frame; absent for a seat that may not seek. */
  onGo?: () => void;
  /** Opens an object's page; absent where selecting objects is switched off. */
  onObject?: (id: string) => void;
  onEvent: (item: EventItem) => void;
}) {
  const [sort, setSort] = useState<SortOf<ObjectSort>>({ key: "", descending: false });
  const copied = useCopied(`event:${item.id}`, (id) => (id === "kind" ? item.label : undefined));
  const withHistory = !live;
  const objects = useMemo(() => sorted(detail.objects, sort), [detail, sort]);
  const columns = useMemo<readonly Column<InvolvedWire>[]>(
    () => [
      {
        key: "id",
        title: "ID",
        kind: "id",
        text: (row) => row.id,
        category: (row) => row.classes[0] ?? UNKNOWN_CLASS,
        sortable: true,
      },
      {
        key: "class",
        title: "Class",
        kind: "text",
        text: classOf,
        sortable: true,
        stretch: !withHistory,
      },
      ...(withHistory
        ? ([
            {
              key: "presence",
              title: "Presence",
              kind: "presence",
              render: (row) => (
                <PresenceStrip
                  spans={row.presence}
                  frames={frames}
                  category={row.classes[0] ?? UNKNOWN_CLASS}
                  ticks={[item.frame]}
                  current={current}
                />
              ),
              stretch: true,
            },
            {
              key: "seen",
              title: "Seen",
              kind: "mono",
              text: (row) => (row.seen ? SEEN(row.seen[0], row.seen[1], timeOf) : ""),
              sortable: true,
              priority: 1,
            },
          ] satisfies Column<InvolvedWire>[])
        : []),
    ],
    [withHistory, frames, current, timeOf, item.frame],
  );
  const moment = detail.sameMoment;
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <dl className="m-0 shrink-0 py-xs">
        <Field label="kind">
          {item.kind !== "gap" && (
            <span
              aria-hidden
              className={`size-m shrink-0 rounded-full ${categoryDot(item.kind)}`}
            />
          )}
          <span className="shrink-0 font-semibold">{kindText(item)}</span>
          <span className="min-w-0 flex-1 truncate text-muted" title={item.label}>
            {summaryOf(item)}
          </span>
          <IconButton
            icon={copied.is("kind") ? "check" : "copy"}
            label="Copy kind"
            onClick={() => copied.copy("kind", item.label)}
          />
        </Field>
        <Field label="time">
          <span className="font-mono tabular-nums">
            {item.timeUs === undefined ? "" : clockOf(item.timeUs)}
          </span>
        </Field>
        <Field label="frame">
          <span className="min-w-0 flex-1 font-mono tabular-nums">{`#${item.frame}`}</span>
          {!live && (
            <Button size="small" disabled={!onGo} onClick={onGo}>
              Go to frame
            </Button>
          )}
        </Field>
      </dl>
      {message}
      {objects.length > 0 && (
        <section aria-label="Objects involved" className="shrink-0">
          <SectionCaption heading="Objects involved" count={objects.length} />
          <Table<InvolvedWire>
            tableKey="event-objects"
            label="Objects involved"
            columns={columns}
            count={objects.length}
            rowAt={(index) => objects[index]}
            keyOf={(row) => row.id}
            indexOf={(key) => objects.findIndex((row) => row.id === key)}
            selected={null}
            dimmed={(row) => present !== null && !present.has(row.id)}
            copyText={(row) => row.id}
            sort={sort}
            onSort={(key) => setSort(nextSort(sort, key as ObjectSort))}
            onActivate={(row) => onObject?.(row.id)}
            cursorId={(row) => `row:event-object-${row.id}`}
          />
        </section>
      )}
      {moment.length > 0 && (
        <section aria-label="Same moment" className="shrink-0">
          <SectionCaption
            heading="Same moment"
            count={moment.length}
            note={detail.truncated ? "more not shown" : undefined}
          />
          <Table<EventItem>
            tableKey="event-moment"
            label="Same moment"
            columns={MOMENT_COLUMNS}
            count={moment.length}
            rowAt={(index) => moment[index]}
            keyOf={(row) => String(row.id)}
            indexOf={(key) => moment.findIndex((row) => String(row.id) === key)}
            selected={null}
            sort={{ key: "", descending: false }}
            onSort={() => undefined}
            onActivate={onEvent}
            cursorId={(row) => `row:event-${row.id}`}
          />
        </section>
      )}
    </div>
  );
}
