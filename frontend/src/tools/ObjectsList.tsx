import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import type { FileRowWire, SearchWire } from "../generated/api";
import { Button } from "../kit/Button";
import { SegmentedControl } from "../kit/Chips";
import type { LaneFrame } from "../playback/contract";
import { readyOf } from "../playback/fileBasis";
import type { FileEvents } from "../playback/fileEvents";
import type { FileScope } from "../playback/fileScope";
import type { FrameFields } from "../playback/frameFields";
import type { AppearanceSource } from "../playback/useAppearance";
import type { PlaybackView } from "../playback/view";
import { matches, missingPaths } from "../search/match";
import { parseSearch } from "../search/query";
import {
  drawnIds,
  frameObjects,
  inspectedPaths,
  isFileSort,
  recordOf,
  sortObjects,
  type FrameObject,
  type SortKey,
} from "./objects";
import { neighbour, type Steps } from "./pageStack";
import { NO_STEPS, UnderPage, type Open } from "./UnderPage";
import { FRAME_COLUMNS, ShownFrame, fileColumns } from "./objectColumns";
import { openRow } from "./openRow";
import { SCOPES, type ListState, type Scope } from "./listState";
import { leavesMessage, listMessage, noticeOf, staleOf } from "./listStatus";
import { MiddleElided } from "../kit/MiddleElided";
import { SearchBar } from "./SearchBar";
import { nextSort } from "./table/columns";
import { Table, type TableMemory } from "./table/Table";
import { useApplied } from "./useApplied";

/** What the panel reads of a viewer and asks of it: `PlaybackSession` is one. */
export interface ObjectSource {
  readonly files: Pick<
    FileScope,
    "request" | "flush" | "locating" | "release" | "subscribe" | "view" | "basis"
  > &
    AppearanceSource & {
      /** The lane's recorded events, which the Events tab lists. */
      readonly events: Pick<
        FileEvents,
        "view" | "request" | "flush" | "release" | "want" | "event" | "eventUnavailable" | "basis"
      >;
    };
  /** The leaves of the frame's objects that a Frame scope search names. */
  readonly fields: Pick<FrameFields, "ask" | "subscribe" | "view">;
  /** What the lane drew on the frame on screen. */
  shown(lane: number): LaneFrame | undefined;
  frameTime(frame: number): number | undefined;
  seek(frame: number): void;
}

/** Rows asked of the backend at once, and how many rows the window moves before another page is asked for. */
const PAGE = 200;
const STEP = 50;
const NO_PATHS: readonly string[] = [];
const NO_OBJECTS: readonly FrameObject[] = [];
/** How many marks a presence strip can draw. */
const BINS = 64;

const SCOPE_KEYS = Object.keys(SCOPES) as Scope[];
/** What the scope control says on hover, the scopes together as it has no tooltip per option. */
const scopeHints = SCOPE_KEYS.map((key) => `${SCOPES[key].title}: ${SCOPES[key].hint}`).join("\n");

/**
 * The list of one lane's objects, in Frame scope or File scope. While a page is `open` the list stays as it is, out of
 * reach under the page, which is drawn with the objects either side of `from` in the list's current order. `header` is
 * what stands above the list: the panel's tabs.
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
  header,
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
  /** What stands above the list: the panel's tabs, the lane to list and the button that closes the panel. */
  header: ReactNode;
}) {
  const { text, sorts, anchor } = list;
  // File scope needs the lane's file read: while it is not (loading, failed, described again after a reconnect) Frame
  // shows and the switch is not offered, and the scope the user chose comes back with the file. A lane with no file at
  // all (`source === null`) is never listed here.
  const hasFile = !frameOnly && readyOf(view.lanes[lane]) !== undefined;
  const scope = hasFile ? list.scope : "frame";
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
  // Frame scope searches the leaves the backend reads at the paths the search names, for the sample on screen.
  const paths = useMemo(() => (inFile ? NO_PATHS : inspectedPaths(wire)), [inFile, wire]);
  const leaves = useSyncExternalStore(source.fields.subscribe, () =>
    source.fields.view(frame, paths),
  );
  // `leaves` is a dependency so that a connection that forgot them is asked again.
  useEffect(() => {
    source.fields.ask(frame, paths);
  }, [source, frame, paths, leaves]);
  const ready = leaves.state === "ready" ? leaves.leaves : null;
  const matched = useMemo(
    () =>
      ready === null
        ? []
        : sortObjects(
            objects.filter((object) => matches(wire, recordOf(object, ready))),
            sorts.frame,
          ),
    [objects, wire, ready, sorts.frame],
  );
  // While the leaves of the sample on screen are awaited, the last completed result of the same search stays, marked
  // busy; no leaves are lent to the sample. Another search, an empty result and an unavailable search keep no rows.
  const [kept, keep] = useState<{ wire: SearchWire; found: readonly FrameObject[] } | null>(null);
  if (ready !== null && kept?.found !== matched) keep({ wire, found: matched });
  const found =
    ready !== null
      ? matched
      : leaves.state === "pending" && kept?.wire === wire && kept.found.length > 0
        ? kept.found
        : NO_OBJECTS;
  const foundAt = useMemo(() => new Map(found.map(({ id }, index) => [id, index])), [found]);
  const missing = useMemo(
    () =>
      ready === null
        ? []
        : missingPaths(
            wire,
            objects.map((object) => recordOf(object, ready)),
          ),
    [objects, wire, ready],
  );
  const notice = useMemo(() => noticeOf(inFile, wire, missing), [inFile, wire, missing]);
  const files = useSyncExternalStore(source.files.subscribe, () => source.files.view(lane));
  const { page } = files;
  const stale = inFile ? staleOf(files) : null;
  const total = inFile ? (page?.total ?? 0) : objects.length;
  // The header counts Frame scope against what the frame holds before the lane's toggles and filter hide any.
  const whole = inFile ? total : (frame?.columns.rows ?? 0);
  const count = inFile ? (page?.matched ?? 0) : found.length;
  const shownFrame = view.shown?.sequenceId;
  const fileCols = useMemo(
    () => fileColumns(view.frameCount, (frame) => source.frameTime(frame)),
    [view.frameCount, source],
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
    const step = (delta: number) => {
      const entity = neighbour(rows, from, delta);
      return entity === null ? null : ({ kind: "object", entity } as const);
    };
    return { previous: step(-1), next: step(1) };
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

  // Rows kept while the leaves are awaited are not a completed search; with none to keep, the wait is said.
  const awaiting = leaves.state !== "ready";
  const message = !awaiting
    ? listMessage(scope, inFile, files.unavailable, page !== null, count, total)
    : found === NO_OBJECTS
      ? leavesMessage(leaves.state)
      : null;
  const shared = {
    label: "Objects",
    count,
    selected: mark,
    sort,
    generation: `${text}\n${sort.key} ${sort.descending}\n${inFile ? source.files.basis(lane) : ""}`,
    onSort: (key: string) =>
      // Only the columns that sort have a header to click.
      onList({
        ...list,
        sorts: {
          ...sorts,
          [scope]: nextSort(sort, key as SortKey),
        },
      }),
    busy: inFile ? files.pending : leaves.state === "pending",
    message,
  };

  return (
    <UnderPage open={open} steps={steps}>
      {header}
      <div className="flex items-center gap-m border-b border-line py-xs pr-xs pl-m">
        {hasFile && (
          <div className="min-w-0" title={scopeHints}>
            <SegmentedControl
              label="Scope"
              options={SCOPE_KEYS.map((key) => [key, SCOPES[key].title] as const)}
              value={scope}
              onChange={(key) => onList({ ...list, scope: key })}
            />
          </div>
        )}
        <span className="min-w-0 flex-1 truncate text-right text-small text-muted">{`${awaiting && found !== NO_OBJECTS ? `${count}…` : count} of ${whole}`}</span>
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
        <ShownFrame value={shownFrame}>
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
        </ShownFrame>
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
    </UnderPage>
  );
}
