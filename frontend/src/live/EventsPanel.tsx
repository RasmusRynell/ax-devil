import { useMemo, useState, useSyncExternalStore, type CSSProperties } from "react";
import { IconButton } from "../kit/IconButton";
import { PanelDivider } from "../tools/PanelDivider";
import type { Column } from "../tools/table/columns";
import { Table } from "../tools/table/Table";
import type { EventLog, LogEntry } from "./eventLog";
import { utcTime } from "./words";

/** The characters of a `HH:MM:SS.ffffff` time, which its column never shrinks below. */
const TIME_CHARS = 15;

const COLUMNS: readonly Column<LogEntry>[] = [
  {
    key: "time",
    title: "Time (UTC)",
    kind: "text",
    text: (entry) => utcTime(entry.timeUs),
    least: TIME_CHARS,
  },
  {
    key: "event",
    title: "Event",
    kind: "text",
    text: (entry) => (entry.type === "gap" ? "Samples lost" : entry.label),
    stretch: true,
  },
  {
    key: "objects",
    title: "Objects",
    kind: "text",
    text: (entry) => (entry.type === "gap" ? "" : entry.entities.join(", ")),
    priority: 1,
  },
];

const SORT = { key: "time", descending: true };

/**
 * The recent events of a live stream, newest first, with a marker where the overlay's decoder fell behind and
 * samples were lost. The list lives in the session and keeps recording while this panel is closed; it holds a fixed
 * number of entries and says how many older ones were dropped. Laid out as the objects panel is: a column beside the
 * picture when the viewer is wide enough, else a cover; `width` is the pixels the user chose, kept by the viewer.
 */
export function EventsPanel({
  log,
  width,
  onWidth,
  onClose,
}: {
  log: EventLog;
  width: number | null;
  onWidth: (width: number) => void;
  onClose: () => void;
}) {
  const [live, setLive] = useState(width);
  const { entries, dropped } = useSyncExternalStore(log.subscribe, log.getSnapshot);
  const rowAt = useMemo(() => (index: number) => entries[entries.length - 1 - index], [entries]);
  return (
    <aside
      aria-label="Events"
      style={live === null ? undefined : ({ "--panel-width": `${live}px` } as CSSProperties)}
      onKeyDown={(event) => {
        if (!event.ctrlKey && !event.altKey && !event.metaKey && event.key !== "Escape")
          event.stopPropagation();
      }}
      onKeyUp={(event) => event.stopPropagation()}
      className="absolute inset-0 z-10 flex flex-col bg-panel text-fg @min-[72ch]:relative @min-[72ch]:w-[var(--panel-width,min(calc(var(--spacing-sidebar)*1.25),60%))] @min-[72ch]:min-w-sidebar @min-[72ch]:border-l @min-[72ch]:border-line"
    >
      <PanelDivider
        onResize={(next, final) => {
          setLive(next);
          if (final) onWidth(next);
        }}
        onClose={onClose}
      />
      <div className="flex items-center gap-m border-b border-line py-xs pr-xs pl-m">
        <span className="min-w-0 flex-1 truncate font-semibold">Events</span>
        <span className="shrink-0 text-small text-muted tabular-nums">{entries.length}</span>
        <IconButton icon="x" label="Close events" onClick={onClose} />
      </div>
      {dropped > 0 && (
        <p role="status" className="m-0 px-m py-xs text-small break-words text-muted">
          {`${dropped} older dropped`}
        </p>
      )}
      <Table<LogEntry>
        tableKey="live-events"
        label="Events"
        columns={COLUMNS}
        count={entries.length}
        rowAt={rowAt}
        keyOf={(entry) => String(entry.id)}
        selected={null}
        indexOf={(key) => {
          const at = entries.findIndex((entry) => String(entry.id) === key);
          return at < 0 ? -1 : entries.length - 1 - at;
        }}
        dimmed={(entry) => entry.type === "gap"}
        sort={SORT}
        onSort={() => undefined}
        onActivate={() => undefined}
        cursorId={(entry) => `row:event-${entry.id}`}
        message={
          entries.length === 0 ? (
            <p role="status" className="m-0 p-m text-muted">
              No events
            </p>
          ) : undefined
        }
      />
    </aside>
  );
}
