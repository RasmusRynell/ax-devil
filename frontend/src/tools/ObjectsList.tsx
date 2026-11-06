import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import type { FileRowWire } from "../generated/api";
import { Button } from "../kit/Button";
import { SegmentedControl } from "../kit/Chips";
import type { LaneFrame } from "../playback/contract";
import type { FileScope } from "../playback/fileScope";
import type { AppearanceSource } from "../playback/useAppearance";
import type { PlaybackView } from "../playback/view";
import { matches, missingPaths } from "../search/match";
import { parseSearch } from "../search/query";
import {
  drawnIds,
  frameObjects,
  inRecord,
  isFileSort,
  recordOf,
  sortObjects,
  type FrameObject,
  type SortKey,
} from "./objects";
import { neighbour, type Steps } from "./pageStack";
import { FRAME_COLUMNS, fileColumns } from "./objectColumns";
import { openRow } from "./openRow";
import { SCOPES, type ListState, type Scope } from "./listState";
import { listMessage, noticeOf, staleOf } from "./listStatus";
import { MiddleElided } from "../kit/MiddleElided";
import { SearchBar } from "./SearchBar";
import { nextSort } from "./table/columns";
import { Table, type TableMemory } from "./table/Table";
import { useApplied } from "./useApplied";

/** What the panel reads of a viewer and asks of it: `PlaybackSession` is one. */
export interface ObjectSource {
  readonly files: Pick<
    FileScope,
    "request" | "flush" | "locating" | "release" | "subscribe" | "view"
  > &
    AppearanceSource;
  /** What the lane drew on the frame on screen. */
  shown(lane: number): LaneFrame | undefined;
  frameTime(frame: number): number | undefined;
  seek(frame: number): void;
}

/** Rows asked of the backend at once, and how many rows the window moves before another page is asked for. */
const PAGE = 200;
const STEP = 50;
/** How many marks a presence strip can draw. */
const BINS = 64;

/** The page drawn over a list, and the object of the list it was opened from, if any. */
export type Open = { from: string | null; render: (steps: Steps) => ReactNode };

const SCOPE_KEYS = Object.keys(SCOPES) as Scope[];
/** What the scope control says on hover, the scopes together as it has no tooltip per option. */
const scopeHints = SCOPE_KEYS.map((key) => `${SCOPES[key].title}: ${SCOPES[key].hint}`).join("\n");

const NO_STEPS: Steps = { previous: null, next: null };

/**
 * The list of one lane's objects, in Frame scope or File scope. While a page is `open` the list stays as it is, out of
 * reach under the page, which is drawn with the objects either side of `from` in the list's current order.
 */
export function ObjectsList({
  source,
  view,
  lane,
  canSeek,
  selectable,
  frameOnly,
  list,
  onList,
  mark,
  open,
  onOpen,
  trailing,
}: {
  source: ObjectSource;
  view: PlaybackView;
  lane: number;
  canSeek: boolean;
  selectable: boolean;
  /** Lists only the frame on screen, for a viewer with no file behind it: a live stream. */
  frameOnly: boolean;
  /** The scope, search and sorts, which the viewer keeps while the panel is closed. */
  list: ListState;
  onList: (list: ListState) => void;
  /** The object whose row is marked: the one a page was last opened from. */
  mark: string | null;
  open: Open | null;
  /** A row was chosen: open the page of its object. */
  onOpen: (entity: string) => void;
  /** What ends the top bar: the lane to list and the button that closes the panel. */
  trailing: ReactNode;
}) {
  const { scope, text, sorts, anchor } = list;
  const [offset, setOffset] = useState(0);
  // Where each scope's table was scrolled and where its cursor was, for when the scope is shown again.
  const tables = useRef<Record<Scope, TableMemory>>({
    frame: { top: 0, cursor: null },
    file: { top: 0, cursor: null },
  });
  const sort = sorts[scope];
  // File scope's request has its own pause; the frame's objects are filtered here, once typing pauses or is committed.
  const [applied, apply] = useApplied(text);
  const [committed, setCommitted] = useState(false);
  const searchBar = useRef<{ focus(): void }>(null);
  const { wire } = useMemo(() => parseSearch(applied), [applied]);
  const frame = source.shown(lane);
  const objects = useMemo(() => frameObjects(frame), [frame]);
  // What is in the frame on screen, whatever the lane's toggles and filter hide; File scope dims the rest, and nothing
  // while no frame is on screen.
  const inFile = scope === "file";
  const onScreen = view.shown !== undefined;
  const drawn = useMemo(
    () => (inFile && onScreen ? drawnIds(frame) : null),
    [inFile, onScreen, frame],
  );
  const absent = (id: string) => drawn !== null && !drawn.has(id);
  const found = useMemo(
    () =>
      sortObjects(
        objects.filter((object) => matches(wire, recordOf(object))),
        sorts.frame,
      ),
    [objects, wire, sorts.frame],
  );
  const foundAt = useMemo(() => new Map(found.map(({ id }, index) => [id, index])), [found]);
  const missing = useMemo(
    () =>
      missingPaths(
        wire,
        objects.map((object) => recordOf(object)),
      ).filter(inRecord),
    [objects, wire],
  );
  const notice = useMemo(() => noticeOf(inFile, wire, missing), [inFile, wire, missing]);
  const files = useSyncExternalStore(source.files.subscribe, () => source.files.view(lane));
  const { page } = files;
  const stale = inFile ? staleOf(files) : null;
  const total = inFile ? (page?.total ?? 0) : objects.length;
  const count = inFile ? (page?.matched ?? 0) : found.length;
  const shownFrame = view.shown?.sequenceId;
  const fileCols = useMemo(
    () => fileColumns(view.frameCount, (frame) => source.frameTime(frame), shownFrame),
    [view.frameCount, shownFrame, source],
  );
  // The rows in view, bucketed so that scrolling asks the backend for a page only now and then.
  const onRange = useCallback((start: number) => setOffset(Math.floor(start / STEP) * STEP), []);

  // An open page steps to the rows either side of its own, so the rows around it are the ones asked for: where the
  // backend puts its row in the list, which the class toggles, the search and the data change, or where it was when
  // the panel last had it.
  const from = open?.from ?? null;
  const { row, near } = openRow(from, inFile ? page : null, anchor);
  const wanted =
    inFile && near !== null ? Math.max(0, Math.floor(near / STEP) * STEP - STEP) : offset;
  useEffect(() => {
    if (from !== null && row !== null && (anchor?.entity !== from || anchor.row !== row))
      onList({ ...list, anchor: { entity: from, row } });
  }, [from, row, anchor, list, onList]);
  const steps = useMemo<Steps>(() => {
    if (from === null) return NO_STEPS;
    const rows = inFile
      ? { keys: page?.rows.map(({ id }) => id) ?? [], offset: page?.offset ?? 0, matched: count }
      : { keys: found.map(({ id }) => id), offset: 0, matched: found.length };
    return { previous: neighbour(rows, from, -1), next: neighbour(rows, from, 1) };
  }, [from, inFile, page, count, found]);

  // File scope asks for the rows in view, and stops when another scope shows or the panel goes away.
  useEffect(() => {
    if (inFile && isFileSort(sort))
      // Moving the cursor asks for nothing; the next request names where it is, for the table to find it.
      source.files.request(
        lane,
        text,
        sort,
        wanted,
        PAGE,
        BINS,
        from ?? tables.current.file.cursor,
      );
  }, [source, lane, inFile, text, sort, wanted, from]);
  // Return and Clear ask for the list at once; this runs after the request the committed text made.
  useEffect(() => {
    if (!committed) return;
    source.files.flush(lane);
    setCommitted(false);
  }, [source, lane, committed]);
  useEffect(() => {
    if (inFile) return () => source.files.release(lane);
  }, [source, lane, inFile]);

  const choose = (id: string, goTo?: number) => {
    if (selectable) onOpen(id);
    // An object not on screen cannot be inspected; going to where it first is makes it so.
    if (goTo !== undefined && canSeek) source.seek(goTo);
  };

  const message = listMessage(scope, inFile, files.unavailable, page !== null, count, total);
  const shared = {
    label: "Objects",
    count,
    selected: mark,
    sort,
    onSort: (key: string) =>
      // Only the columns that sort have a header to click.
      onList({
        ...list,
        sorts: {
          ...sorts,
          [scope]: nextSort(sort, key as SortKey),
        },
      }),
    busy: inFile && files.pending,
    message,
  };

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div inert={open !== null} className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center gap-m border-b border-line py-xs pr-xs pl-m">
          {!frameOnly && (
            <div className="min-w-0" title={scopeHints}>
              <SegmentedControl
                label="Scope"
                options={SCOPE_KEYS.map((key) => [key, SCOPES[key].title] as const)}
                value={scope}
                onChange={(key) => onList({ ...list, scope: key })}
              />
            </div>
          )}
          <span className="min-w-0 flex-1 truncate text-right text-small text-muted">{`${count} of ${total}`}</span>
          {trailing}
        </div>
        <div className="p-m pb-s">
          <SearchBar
            handle={searchBar}
            label="Search objects"
            value={text}
            onChange={(value) => onList({ ...list, text: value })}
            onCommit={(value) => {
              apply(value);
              setCommitted(true);
            }}
          />
          {notice !== null && (
            <div role="status" className="pt-xs text-small text-muted">
              <MiddleElided text={notice} />
            </div>
          )}
          {stale !== null && (
            <div role="status" className="flex items-center gap-s pt-xs text-small text-muted">
              <span className="min-w-0 flex-1">{stale}</span>
              <Button
                size="small"
                onClick={() => {
                  source.files.flush(lane);
                  // The button goes when the rows arrive; focus is not left on it to be lost then.
                  searchBar.current?.focus();
                }}
              >
                Retry
              </Button>
            </div>
          )}
        </div>
        {inFile ? (
          <Table<FileRowWire>
            {...shared}
            key="objects-file"
            tableKey="objects-file"
            columns={fileCols}
            rowAt={(index) => page?.rows[index - page.offset]}
            keyOf={(row) => row.id}
            indexOf={(key) => openRow(key, page, null).row ?? -1}
            memory={tables.current.file}
            dimmed={(row) => absent(row.id)}
            onActivate={(row) => choose(row.id, absent(row.id) ? row.first_frame : undefined)}
            onRange={onRange}
            onCursor={(key) => from === null && source.files.locating(lane, key)}
            cursorId={(row) => `row:object-${row.id}`}
          />
        ) : (
          <Table<FrameObject>
            {...shared}
            key="objects-frame"
            tableKey="objects-frame"
            columns={FRAME_COLUMNS}
            rowAt={(index) => found[index]}
            keyOf={(object) => object.id}
            indexOf={(key) => foundAt.get(key) ?? -1}
            memory={tables.current.frame}
            onActivate={(object) => choose(object.id)}
            cursorId={(object) => `row:object-${object.id}`}
          />
        )}
      </div>
      {open && <div className="absolute inset-0 flex flex-col bg-panel">{open.render(steps)}</div>}
    </div>
  );
}
