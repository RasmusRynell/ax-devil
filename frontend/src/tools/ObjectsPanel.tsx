import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type ReactNode,
} from "react";
import { reasonOf } from "../errors";
import type { FileRowWire, FileScopeUnavailable } from "../generated/api";
import { Select } from "../kit/Fields";
import { SegmentedControl } from "../kit/Chips";
import { IconButton } from "../kit/IconButton";
import type { LaneFrame, Selection } from "../playback/contract";
import type { FileScope } from "../playback/fileScope";
import type { PlaybackView } from "../playback/view";
import { CodePoints, codePointString } from "../present/expr/codePoints";
import { emitFixed } from "../present/expr/fixed";
import { matches, missingPaths } from "../search/match";
import { parseSearch } from "../search/query";
import {
  drawnIds,
  frameObjects,
  isFileSort,
  recordOf,
  sortObjects,
  type FrameObject,
  type Sort,
  type SortKey,
} from "./objects";
import { PanelDivider } from "./PanelDivider";
import { SearchBar } from "./SearchBar";
import { nextSort, type Column } from "./table/columns";
import { MiddleCut } from "./table/MiddleCut";
import { Table, type TableMemory } from "./table/Table";

/** What the panel reads of a viewer and asks of it: `PlaybackSession` is one. */
interface ObjectSource {
  readonly files: Pick<FileScope, "request" | "release" | "subscribe" | "view">;
  /** What the lane drew on the frame on screen. */
  shown(lane: number): LaneFrame | undefined;
  select(selection: Selection): void;
  seek(frame: number): void;
}

type Scope = "frame" | "file";

/** What each scope starts sorted by and what an empty list says. */
const SCOPES: Record<Scope, { title: string; sort: Sort; none: string }> = {
  frame: {
    title: "Frame",
    sort: { key: "score", descending: true },
    none: "No objects in this frame",
  },
  file: {
    title: "File",
    sort: { key: "id", descending: false },
    none: "No objects in the file",
  },
};

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

const digits = new CodePoints();
/** A score or confidence to two decimals, rounded as Python prints it; blank when there is none. */
const score = (value: number | null): string => {
  digits.clear();
  return value !== null && emitFixed(value, 2, digits)
    ? codePointString(digits.data, 0, digits.length)
    : "";
};

/** What File scope says when the query names a field it does not search; it searches these. */
const FILE_PATHS: ReadonlySet<string> = new Set(["id", "class", "event"]);
const FILE_NOTE = "File scope filters id, class and event only";

/** What Frame scope lists of an object; the first three are required, Motion goes first when the panel is narrow. */
const FRAME_COLUMNS: readonly Column<FrameObject>[] = [
  { key: "id", title: "ID", kind: "id", text: (object) => object.id, sortable: true },
  {
    key: "class",
    title: "Class",
    kind: "text",
    text: (object) => object.className,
    sortable: true,
    stretch: true,
  },
  {
    key: "score",
    title: "Score",
    kind: "number",
    text: (object) => score(object.score),
    sortable: true,
  },
  {
    key: "confidence",
    title: "Conf",
    kind: "number",
    text: (object) => score(object.confidence),
    sortable: true,
  },
  {
    key: "motion",
    title: "Motion",
    kind: "text",
    text: (object) => object.motion,
    sortable: true,
    priority: 1,
  },
];

/** Where an object is across the video: a mark for each span of frames, and a click goes to that frame. */
function Presence({
  row,
  frames,
  onSeek,
}: {
  row: FileRowWire;
  frames: number;
  onSeek?: (frame: number) => void;
}) {
  return (
    <span
      aria-hidden
      className={`relative block h-icon rounded-control bg-hover ${onSeek ? "cursor-pointer" : ""}`}
      onClick={(event) => {
        if (!onSeek) return;
        event.stopPropagation();
        const box = event.currentTarget.getBoundingClientRect();
        onSeek(
          Math.min(
            frames - 1,
            Math.max(0, Math.floor(((event.clientX - box.left) / box.width) * frames)),
          ),
        );
      }}
    >
      {row.presence.map(([from, to]) => (
        <span
          key={from}
          className="absolute inset-y-0 min-w-hairline bg-muted"
          style={{
            left: `${(from / frames) * 100}%`,
            width: `${((to - from + 1) / frames) * 100}%`,
          }}
        />
      ))}
    </span>
  );
}

/** What File scope lists of an object: where it is across the video, and which frames that spans. */
const fileColumns = (frames: number, seek?: (frame: number) => void): Column<FileRowWire>[] => [
  { key: "id", title: "ID", kind: "id", text: (row) => row.id, sortable: true },
  {
    key: "class",
    title: "Class",
    kind: "text",
    text: (row) => row.classes.join(", "),
    sortable: true,
  },
  {
    key: "presence",
    title: "Presence",
    kind: "presence",
    render: (row) => <Presence row={row} frames={frames} onSeek={seek} />,
    stretch: true,
  },
  {
    key: "first_seen",
    title: "Seen",
    kind: "mono",
    text: (row) => `${row.first_frame}–${row.last_frame}`,
    sortable: true,
    priority: 1,
  },
];

/** The list of one lane's objects, in Frame scope or File scope. */
function ObjectsList({
  source,
  view,
  lane,
  canSeek,
  selectable,
  trailing,
}: {
  source: ObjectSource;
  view: PlaybackView;
  lane: number;
  canSeek: boolean;
  selectable: boolean;
  /** What ends the top bar: the lane to list and the button that closes the panel. */
  trailing: ReactNode;
}) {
  const [scope, setScope] = useState<Scope>("frame");
  const [text, setText] = useState("");
  const [sorts, setSorts] = useState<Record<Scope, Sort>>({
    frame: SCOPES.frame.sort,
    file: SCOPES.file.sort,
  });
  const [offset, setOffset] = useState(0);
  // Where each scope's table was scrolled and where its cursor was, for when the scope is shown again.
  const memory = useRef<Record<Scope, TableMemory>>({
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
      ),
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

  // File scope asks for the rows in view, and stops asking when it is not the scope shown or the panel goes away.
  useEffect(() => {
    if (inFile && isFileSort(sort)) source.files.request(lane, text, sort, offset, PAGE, BINS);
  }, [source, lane, inFile, text, sort, offset]);
  useEffect(() => {
    if (inFile) return () => source.files.release(lane);
  }, [source, lane, inFile]);

  const laneSource = view.lanes[lane].source;
  const choose = (id: string, goTo?: number) => {
    if (selectable && laneSource !== null) source.select({ lane, source: laneSource, entity: id });
    // An object that is not on screen cannot be inspected; going to where it first is makes it so.
    if (goTo !== undefined && canSeek) source.seek(goTo);
  };

  const selected = view.selection?.lane === lane ? view.selection.entity : null;
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
    selected,
    sort,
    onSort: (key: string) =>
      // Only the columns that sort have a header to click.
      setSorts({
        ...sorts,
        [scope]: nextSort(sort, key as SortKey),
      }),
    busy: inFile && files.pending,
    message,
  };

  return (
    <>
      <div className="flex items-center gap-m border-b border-line py-xs pr-xs pl-m">
        <SegmentedControl
          label="Scope"
          options={(Object.keys(SCOPES) as Scope[]).map((key) => [key, SCOPES[key].title] as const)}
          value={scope}
          onChange={setScope}
        />
        <span className="min-w-0 flex-1 truncate text-right text-small text-muted">{`${count} of ${total}`}</span>
        {trailing}
      </div>
      <div className="p-m pb-s">
        <SearchBar label="Search objects" value={text} onChange={setText} />
        {notice !== null && (
          <div role="status" className="pt-xs text-small text-muted">
            <MiddleCut text={notice} tail={13} />
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
          memory={memory.current.file}
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
          memory={memory.current.frame}
          onActivate={(object) => choose(object.id)}
          cursorId={(object) => `row:object-${object.id}`}
        />
      )}
    </>
  );
}

/**
 * The media tools of a viewer: a searchable list of the objects of one lane, in the frame on screen or in the whole
 * file. A row selects its object; one that is not in the frame on screen also goes to where it first is. A column
 * beside the picture when the viewer, a size container that is its nearest positioned ancestor, is wide enough; else it
 * covers the whole viewer. Beside the picture, a divider on its left edge resizes it; `width` is the pixels the user
 * chose, for the viewer to keep while the panel is closed, and `onWidth` hears each choice.
 */
export function ObjectsPanel({
  source,
  view,
  canSeek,
  selectable,
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
  /** The width the user gave the panel, or `null` for the default. */
  width: number | null;
  onWidth: (width: number) => void;
  onClose: () => void;
}) {
  const [live, setLive] = useState(width);
  const lanes = view.lanes.flatMap((lane, index) => (lane.source === null ? [] : [index]));
  const [chosen, setChosen] = useState<number | null>(null);
  const lane = chosen !== null && lanes.includes(chosen) ? chosen : lanes[0];
  const close = <IconButton icon="x" label="Close media tools" onClick={onClose} />;
  return (
    <aside
      aria-label="Media tools"
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
