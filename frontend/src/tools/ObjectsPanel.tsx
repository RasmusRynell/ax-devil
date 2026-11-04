import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from "react";
import { reasonOf } from "../errors";
import type { FileRowWire, FileScopeUnavailable } from "../generated/api";
import { Select } from "../kit/Fields";
import { SegmentedControl } from "../kit/Chips";
import { IconButton } from "../kit/IconButton";
import type { LaneFrame } from "../playback/contract";
import type { FileScope } from "../playback/fileScope";
import type { InspectorMemory } from "../playback/fields";
import { Inspector, reachOf } from "../playback/Inspector";
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
import { back, neighbour, push, step, titleOf, type PageStack, type Steps } from "./pageStack";
import { FRAME_COLUMNS, fileColumns } from "./objectColumns";
import { openRow } from "./openRow";
import { PageHeader } from "./PageHeader";
import { SCOPES, type ListState, type Scope } from "./listState";
import { MiddleElided } from "../kit/MiddleElided";
import { PanelDivider } from "./PanelDivider";
import { SearchBar } from "./SearchBar";
import { nextSort } from "./table/columns";
import { Table, type TableMemory } from "./table/Table";

/** What the panel reads of a viewer and asks of it: `PlaybackSession` is one. */
interface ObjectSource {
  readonly files: Pick<FileScope, "request" | "release" | "subscribe" | "view"> & AppearanceSource;
  /** What the lane drew on the frame on screen. */
  shown(lane: number): LaneFrame | undefined;
  frameTime(frame: number): number | undefined;
  seek(frame: number): void;
}

/** The lane whose object a page is about, and the source the lane reads. */
export type Place = { lane: number; source: number };

/** What File scope says when the backend has no rows, but for a failure, which says why. */
const UNAVAILABLE: Record<Exclude<FileScopeUnavailable["state"], "Failed">, string> = {
  Reading: "Reading the file…",
  Following: "Not available while following",
  TooLarge: "The file is too large to list",
  PlainLane: "This lane has no objects",
};

/** Rows asked of the backend at once, and how many rows the window moves before another page is asked for. */
const PAGE = 200;
const STEP = 50;
/** How many marks a presence strip can draw. */
const BINS = 64;

/** What File scope says when the query names a field it does not search; it searches these. */
const FILE_PATHS: ReadonlySet<string> = new Set(["id", "class", "event"]);
const FILE_NOTE = "File scope filters id, class and event only";

/** The page drawn over a list, and the object of the list it was opened from, if any. */
type Open = { from: string | null; render: (steps: Steps) => ReactNode };

const NO_STEPS: Steps = { previous: null, next: null };

/**
 * The list of one lane's objects, in Frame scope or File scope. While a page is `open` the list stays as it is, out of
 * reach under the page, which is drawn with the objects either side of `from` in the list's current order.
 */
function ObjectsList({
  source,
  view,
  lane,
  canSeek,
  selectable,
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
  const { wire } = useMemo(() => parseSearch(text), [text]);
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
  const fileNote = useMemo(
    () =>
      wire.groups.flat().some(({ path }) => path !== null && !FILE_PATHS.has(path.toLowerCase())),
    [wire],
  );
  const notice = inFile
    ? fileNote
      ? FILE_NOTE
      : null
    : missing.length > 0
      ? `No object has ${missing.join(", ")} in this frame`
      : null;
  const files = useSyncExternalStore(source.files.subscribe, () => source.files.view(lane));
  const { page } = files;
  const total = inFile ? (page?.total ?? 0) : objects.length;
  const count = inFile ? (page?.matched ?? 0) : found.length;
  const seek = canSeek ? (frameNumber: number) => source.seek(frameNumber) : undefined;
  const fileCols = useMemo(
    () => fileColumns(view.frameCount, seek),
    [view.frameCount, canSeek, source],
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
      source.files.request(lane, text, sort, wanted, PAGE, BINS, from);
  }, [source, lane, inFile, text, sort, wanted, from]);
  useEffect(() => {
    if (inFile) return () => source.files.release(lane);
  }, [source, lane, inFile]);

  const choose = (id: string, goTo?: number) => {
    if (selectable) onOpen(id);
    // An object not on screen cannot be inspected; going to where it first is makes it so.
    if (goTo !== undefined && canSeek) source.seek(goTo);
  };

  let message: ReactNode = null;
  if (inFile && files.unavailable)
    message = (
      <p
        role="status"
        aria-busy={files.unavailable.state === "Reading"}
        className="m-0 p-m text-muted"
      >
        {files.unavailable.state === "Failed"
          ? reasonOf(files.unavailable)
          : UNAVAILABLE[files.unavailable.state]}
      </p>
    );
  else if (inFile && !page)
    message = (
      <p role="status" aria-busy className="m-0 p-m text-muted">
        Loading…
      </p>
    );
  else if (count === 0)
    message = (
      <p role="status" className="m-0 pt-l text-center text-small text-muted">
        {total === 0 ? SCOPES[scope].none : "No matching objects"}
      </p>
    );
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
          <SegmentedControl
            label="Scope"
            options={(Object.keys(SCOPES) as Scope[]).map(
              (key) => [key, SCOPES[key].title] as const,
            )}
            value={scope}
            onChange={(key) => onList({ ...list, scope: key })}
          />
          <span className="min-w-0 flex-1 truncate text-right text-small text-muted">{`${count} of ${total}`}</span>
          {trailing}
        </div>
        <div className="p-m pb-s">
          <SearchBar
            label="Search objects"
            value={text}
            onChange={(value) => onList({ ...list, text: value })}
          />
          {notice !== null && (
            <div role="status" className="pt-xs text-small text-muted">
              <MiddleElided text={notice} />
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
            indexOf={(key) => {
              const at = page ? page.rows.findIndex((row) => row.id === key) : -1;
              return at < 0 ? -1 : page!.offset + at;
            }}
            memory={tables.current.file}
            dimmed={(row) => absent(row.id)}
            onActivate={(row) => choose(row.id, absent(row.id) ? row.first_frame : undefined)}
            onRange={onRange}
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

/** Whether keys typed at `target` belong to the text being edited there. */
const editing = (target: EventTarget): boolean =>
  target instanceof HTMLElement &&
  (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

/**
 * The media tools of a viewer: a searchable list of the objects of one lane, in the frame on screen or in the whole
 * file. A row opens the page of its object over the list, with Back, and Previous and Next through the list as it is
 * now; one that is not in the frame on screen also goes to where it first is. `pages` is the page stack, which the
 * viewer keeps while the panel is closed, and `onNavigate` hears each change of it with the lane it is about. Esc,
 * Backspace and the mouse's Back button go back one page. A column beside the picture when the viewer, a size
 * container that is its nearest positioned ancestor, is wide enough; else it covers the whole viewer. Beside the
 * picture, a divider on its left edge resizes it; `width` is the pixels the user chose, for the viewer to keep while
 * the panel is closed, and `onWidth` hears each choice.
 */
export function ObjectsPanel({
  source,
  view,
  canSeek,
  selectable,
  pages,
  onNavigate,
  list,
  onList,
  memory,
  onMemory,
  width,
  onWidth,
  onClose,
}: {
  source: ObjectSource;
  view: PlaybackView;
  /** Whether this seat may move the viewer's position. */
  canSeek: boolean;
  /** Whether selecting an object is switched on. */
  selectable: boolean;
  pages: PageStack;
  onNavigate: (pages: PageStack, at: Place) => void;
  /** What the viewer keeps of the objects list: the page steps through the list it was opened from. */
  list: ListState;
  onList: (list: ListState) => void;
  /** What the viewer keeps of the object page: pinned fields and folded groups. */
  memory: InspectorMemory;
  onMemory: (memory: InspectorMemory) => void;
  /** The width the user gave the panel, or `null` for the default. */
  width: number | null;
  onWidth: (width: number) => void;
  onClose: () => void;
}) {
  const [live, setLive] = useState(width);
  const lanes = view.lanes.flatMap((lane, index) => (lane.source === null ? [] : [index]));
  // An object picked on another lane's picture switches the panel to that lane, as does opening after it.
  const picked = view.selection?.lane;
  const [chosen, setChosen] = useState<number | null>(picked ?? null);
  const [seen, setSeen] = useState(picked);
  if (picked !== seen) {
    setSeen(picked);
    if (picked !== undefined && lanes.includes(picked)) setChosen(picked);
  }
  const lane = chosen !== null && lanes.includes(chosen) ? chosen : lanes[0];
  const close = <IconButton icon="x" label="Close media tools" onClick={onClose} />;

  const aside = useRef<HTMLElement>(null);
  const page = useRef<HTMLElement>(null);
  const last = pages.entries.at(-1);
  const below = pages.entries.at(-2);
  // Keyboard focus follows a page that opens or closes only when it was in the panel, not on the video.
  const focused = useRef(false);
  const shown = `${pages.entries.length}:${last?.key.entity}`;
  const was = useRef(shown);
  useLayoutEffect(() => {
    const changed = was.current !== shown;
    const inside = focused.current;
    was.current = shown;
    focused.current = false;
    // Whoever is working the header's buttons keeps them.
    if (!changed || !inside || document.activeElement?.closest("header")) return;
    const list = aside.current?.querySelector<HTMLElement>('[role="listbox"]');
    (page.current ?? list ?? aside.current)?.focus({ preventScroll: true });
  });
  const go = (next: PageStack) => {
    const laneSource = lane === undefined ? null : view.lanes[lane].source;
    if (lane === undefined || laneSource === null) return;
    focused.current = aside.current?.contains(document.activeElement) ?? false;
    onNavigate(next, { lane, source: laneSource });
  };
  const goBack = () => go(back(pages));
  const swallow = useRef(false);

  const onKeyDown = (event: KeyboardEvent) => {
    const plain = !event.ctrlKey && !event.altKey && !event.metaKey;
    const own = plain && !event.shiftKey && (event.key === "Escape" || event.key === "Backspace");
    // The viewer's own Escape does not run: it is spent going back.
    if (own && last && !event.nativeEvent.defaultPrevented && !editing(event.target)) {
      event.preventDefault();
      event.stopPropagation();
      goBack();
    } else if (plain && event.key !== "Escape") event.stopPropagation();
  };
  // The mouse's Back button goes back on press; its release is not the browser's to turn into history.
  const onMouse = (event: MouseEvent) => {
    if (event.button !== 3) return;
    if (event.type === "mousedown") swallow.current = last !== undefined;
    if (!swallow.current) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.type === "mousedown") goBack();
  };

  const open: Open | null = last
    ? {
        from: last.origin ? last.key.entity : null,
        render: (steps) => (
          <section
            ref={page}
            tabIndex={-1}
            aria-label="Object page"
            className="flex min-h-0 flex-1 flex-col outline-none"
          >
            <PageHeader
              title={titleOf(last.key)}
              below={below ? titleOf(below.key) : "Objects"}
              steps={steps}
              onBack={goBack}
              onStep={(entity) => go(step(pages, { kind: "object", entity }))}
            />
            {view.inspection.state !== "none" && (
              <Inspector
                inspection={view.inspection}
                memory={memory}
                onMemory={onMemory}
                reach={reachOf(source, view, canSeek)}
              />
            )}
          </section>
        ),
      }
    : null;

  return (
    <aside
      ref={aside}
      aria-label="Media tools"
      style={live === null ? undefined : ({ "--panel-width": `${live}px` } as CSSProperties)}
      onKeyDown={onKeyDown}
      onKeyUp={(event) => event.stopPropagation()}
      onMouseDown={onMouse}
      onMouseUp={onMouse}
      onAuxClick={onMouse}
      className="absolute inset-0 z-10 flex flex-col bg-panel text-fg @min-[72ch]:relative @min-[72ch]:w-[var(--panel-width,min(var(--spacing-panel),50%))] @min-[72ch]:min-w-sidebar @min-[72ch]:border-l @min-[72ch]:border-line"
    >
      <PanelDivider
        onResize={(next, final) => {
          setLive(next);
          if (final) onWidth(next);
        }}
        onClose={onClose}
      />
      {lane === undefined ? (
        <>
          <div className="flex justify-end border-b border-line py-xs pr-xs">{close}</div>
          <p className="m-0 p-m text-muted">No lane has objects</p>
        </>
      ) : (
        <ObjectsList
          key={lane}
          source={source}
          view={view}
          lane={lane}
          canSeek={canSeek}
          selectable={selectable}
          list={list}
          onList={onList}
          mark={pages.mark?.entity ?? null}
          open={open}
          onOpen={(entity) => go(push(pages, { kind: "object", entity }))}
          trailing={
            <>
              {lanes.length > 1 && (
                <Select
                  label={<span className="sr-only">Lane</span>}
                  value={String(lane)}
                  options={lanes.map((index) => [String(index), view.lanes[index].label] as const)}
                  onChange={(value) => setChosen(Number(value))}
                />
              )}
              {close}
            </>
          }
        />
      )}
    </aside>
  );
}
