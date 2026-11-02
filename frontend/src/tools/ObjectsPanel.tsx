import {
  useEffect,
  useId,
  useMemo,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { reasonOf } from "../errors";
import type { FileRowWire, FileScopeUnavailable } from "../generated/api";
import { Select } from "../kit/Fields";
import { Icon } from "../kit/Icon";
import { IconButton } from "../kit/IconButton";
import type { LaneFrame, Selection } from "../playback/contract";
import type { FileScope } from "../playback/fileScope";
import type { PlaybackView } from "../playback/view";
import { matches } from "../search/match";
import { parseSearch } from "../search/query";
import { useRowWindow } from "../shell/useRowWindow";
import {
  frameObjects,
  isFileSort,
  recordOf,
  sortObjects,
  type Sort,
  type SortKey,
} from "./objects";
import { SearchBar } from "./SearchBar";

/** What the panel reads of a viewer and asks of it: `PlaybackSession` is one. */
interface ObjectSource {
  readonly files: Pick<FileScope, "request" | "release" | "subscribe" | "view">;
  /** What the lane drew on the frame on screen. */
  shown(lane: number): LaneFrame | undefined;
  select(selection: Selection): void;
  seek(frame: number): void;
}

type Scope = "frame" | "file";

/** What each scope lists: its columns (the last sorts by what it shows), how it starts sorted and what an empty list says. */
const SCOPES: Record<
  Scope,
  { title: string; columns: readonly (readonly [SortKey, string])[]; sort: Sort; none: string }
> = {
  frame: {
    title: "Frame",
    columns: [
      ["id", "ID"],
      ["class", "Class"],
      ["score", "Score"],
    ],
    sort: { key: "score", descending: true },
    none: "No objects in this frame",
  },
  file: {
    title: "File",
    columns: [
      ["id", "ID"],
      ["class", "Class"],
      ["first_seen", "Seen"],
    ],
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

/** The fields File scope can search. */
const FILE_FIELDS = ["id", "class", "event"];

/** Rows asked of the backend at once, and how many rows the window moves before another page is asked for. */
const PAGE = 200;
const STEP = 50;
/** How many marks a presence strip can draw. */
const BINS = 64;

const GRID =
  "grid grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1.2fr)] items-center gap-m px-m";

/** A row as the list draws it, whichever scope it comes from. */
type Entry = {
  id: string;
  className: string;
  /** The last column: a score, or where the object is in the video. */
  last: string | FileRowWire;
  /** The object is not in the frame on screen. */
  absent: boolean;
  /** The frame to go to for an object that is not in the frame on screen. */
  first?: number;
};

const score = (value: number | null): string =>
  value === null ? "" : String(Number(value.toFixed(2)));

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

/** The sort buttons over the columns. */
function Columns({
  scope,
  sort,
  onSort,
}: {
  scope: Scope;
  sort: Sort;
  onSort: (key: SortKey) => void;
}) {
  return (
    <div className={`${GRID} h-row text-small text-muted`}>
      {SCOPES[scope].columns.map(([key, title], index) => {
        const active = sort.key === key;
        return (
          <button
            key={key}
            type="button"
            aria-label={`Sort by ${title}${active ? (sort.descending ? ", descending" : ", ascending") : ""}`}
            aria-pressed={active}
            onClick={() => onSort(key)}
            className={`flex min-w-0 items-center gap-xs rounded-control bg-transparent p-0 hover:text-fg focus-ring ${
              active ? "text-fg" : ""
            } ${index === 2 && scope === "frame" ? "justify-end" : ""}`}
          >
            <span className="truncate">{title}</span>
            {active && (
              <span aria-hidden className={sort.descending ? "" : "rotate-180"}>
                <Icon name="chevron-down" />
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

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
  const base = useId();
  const [scope, setScope] = useState<Scope>("frame");
  const [text, setText] = useState("");
  const [sorts, setSorts] = useState<Record<Scope, Sort>>({
    frame: SCOPES.frame.sort,
    file: SCOPES.file.sort,
  });
  const [active, setActive] = useState(0);
  const sort = sorts[scope];
  const { wire } = useMemo(() => parseSearch(text), [text]);
  const frame = source.shown(lane);
  const objects = useMemo(() => frameObjects(frame), [frame]);
  const present = useMemo(() => new Set(objects.map(({ id }) => id)), [objects]);
  const found = useMemo(
    () =>
      sortObjects(
        objects.filter((object) => matches(wire, recordOf(object))),
        sorts.frame,
      ),
    [objects, wire, sorts.frame],
  );
  const files = useSyncExternalStore(source.files.subscribe, () => source.files.view(lane));
  const inFile = scope === "file";
  const { page } = files;
  const total = inFile ? (page?.total ?? 0) : objects.length;
  const count = inFile ? (page?.matched ?? 0) : found.length;
  const { ref, start, end, update, reveal } = useRowWindow(count, '[role="option"]');
  const offset = Math.floor(start / STEP) * STEP;

  // File scope asks for the rows in view, and stops asking when it is not the scope shown or the panel goes away.
  useEffect(() => {
    if (inFile && isFileSort(sort)) source.files.request(lane, text, sort, offset, PAGE, BINS);
  }, [source, lane, inFile, text, sort, offset]);
  useEffect(() => {
    if (inFile) return () => source.files.release(lane);
  }, [source, lane, inFile]);

  const entryAt = (index: number): Entry | undefined => {
    if (!inFile) {
      const object = found[index];
      return (
        object && {
          id: object.id,
          className: object.className,
          last: score(object.score),
          absent: false,
        }
      );
    }
    const row = page?.rows[index - page.offset];
    return (
      row && {
        id: row.id,
        className: row.classes.join(", "),
        last: row,
        absent: !present.has(row.id),
        first: row.first_frame,
      }
    );
  };
  const laneSource = view.lanes[lane].source;
  const select = (entry: Entry) => {
    if (selectable && laneSource !== null)
      source.select({ lane, source: laneSource, entity: entry.id });
    // An object that is not on screen cannot be inspected; going to where it first is makes it so.
    if (entry.absent && canSeek && entry.first !== undefined) source.seek(entry.first);
  };
  const go = (index: number) => {
    const next = Math.max(0, Math.min(count - 1, index));
    setActive(next);
    reveal(next);
  };
  const at = Math.min(active, Math.max(count - 1, 0));
  const onKeyDown = (event: KeyboardEvent) => {
    const page = end - start;
    const keys: Record<string, () => void> = {
      ArrowDown: () => go(at + 1),
      ArrowUp: () => go(at - 1),
      PageDown: () => go(at + page),
      PageUp: () => go(at - page),
      Home: () => go(0),
      End: () => go(count - 1),
      Enter: () => {
        const entry = entryAt(at);
        if (entry) select(entry);
      },
    };
    keys[event.key === " " ? "Enter" : event.key]?.();
    if (event.key in keys || event.key === " ") event.preventDefault();
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
      <p role="status" className="m-0 p-m text-muted">
        {total === 0 ? SCOPES[scope].none : "No matching objects"}
      </p>
    );
  const outside =
    inFile &&
    wire.groups.some((group) =>
      group.some((term) => term.path !== null && !FILE_FIELDS.includes(term.path.toLowerCase())),
    );

  return (
    <>
      <div className="flex items-center gap-m border-b border-line py-xs pr-xs pl-m">
        <div className="flex rounded-control border border-line-strong">
          {(Object.keys(SCOPES) as Scope[]).map((key) => (
            <button
              key={key}
              type="button"
              aria-pressed={scope === key}
              onClick={() => setScope(key)}
              className={`h-row rounded-control bg-transparent px-m hover:bg-hover focus-ring ${
                scope === key ? "bg-hover text-fg" : "text-muted"
              }`}
            >
              {SCOPES[key].title}
            </button>
          ))}
        </div>
        <span className="min-w-0 flex-1 truncate text-small text-muted">{`${count} of ${total}`}</span>
        {trailing}
      </div>
      <div className="flex flex-col gap-s p-m pb-s">
        <SearchBar label="Search objects" value={text} onChange={setText} />
        {outside && (
          <p className="m-0 text-small text-muted">File scope searches id, class and event</p>
        )}
      </div>
      <Columns
        scope={scope}
        sort={sort}
        onSort={(key) =>
          setSorts({
            ...sorts,
            [scope]: { key, descending: sort.key === key ? !sort.descending : key === "score" },
          })
        }
      />
      <div ref={ref} onScroll={update} className="min-h-0 flex-1 overflow-y-auto">
        {message ?? (
          <div
            role="listbox"
            aria-label="Objects"
            aria-activedescendant={`${base}-${at}`}
            aria-busy={inFile && files.pending}
            tabIndex={0}
            onKeyDown={onKeyDown}
            className="group/list focus:outline-none"
            style={{
              paddingTop: `calc(var(--spacing-row) * ${start})`,
              paddingBottom: `calc(var(--spacing-row) * ${count - end})`,
            }}
          >
            {Array.from({ length: end - start }, (_, i) => {
              const index = start + i;
              const entry = entryAt(index);
              const isActive = index === at;
              return (
                <div
                  key={index}
                  id={`${base}-${index}`}
                  role="option"
                  aria-selected={entry?.id === selected}
                  aria-posinset={index + 1}
                  aria-setsize={count}
                  aria-busy={entry ? undefined : true}
                  title={entry?.absent ? "Not in this frame" : undefined}
                  data-cursor-id={entry && `row:object-${entry.id}`}
                  onClick={() => {
                    setActive(index);
                    if (entry) select(entry);
                  }}
                  className={`${GRID} h-row cursor-pointer border-l-edge hover:bg-hover ${
                    entry?.absent ? "text-muted" : "text-fg"
                  } ${entry?.id === selected ? "border-accent bg-hover" : "border-transparent"} ${
                    isActive
                      ? "group-focus-visible/list:outline-(length:--stroke-focus) group-focus-visible/list:-outline-offset-(--stroke-focus) group-focus-visible/list:outline-accent"
                      : ""
                  }`}
                >
                  {entry && (
                    <>
                      <span className="truncate">{entry.id}</span>
                      <span className="truncate">{entry.className}</span>
                      {typeof entry.last === "string" ? (
                        <span className="truncate text-right">{entry.last}</span>
                      ) : (
                        <Presence
                          row={entry.last}
                          frames={view.frameCount}
                          onSeek={canSeek ? (target) => source.seek(target) : undefined}
                        />
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

/**
 * The media tools of a viewer: a searchable list of the objects of one lane, in the frame on screen or in the whole
 * file. A row selects its object; one that is not in the frame on screen also goes to where it first is. A column
 * beside the picture when the viewer, a size container that is its nearest positioned ancestor, is wide enough; else it
 * covers the whole viewer.
 */
export function ObjectsPanel({
  source,
  view,
  canSeek,
  selectable,
  onClose,
}: {
  source: ObjectSource;
  view: PlaybackView;
  /** Whether this seat may move the viewer's position. */
  canSeek: boolean;
  /** Whether selecting an object is switched on. */
  selectable: boolean;
  onClose: () => void;
}) {
  const lanes = view.lanes.flatMap((lane, index) => (lane.source === null ? [] : [index]));
  const [chosen, setChosen] = useState<number | null>(null);
  const lane = chosen !== null && lanes.includes(chosen) ? chosen : lanes[0];
  const close = (
    <IconButton icon="x" label="Close media tools" onClick={onClose} className="size-row" />
  );
  return (
    <aside
      aria-label="Media tools"
      onKeyDown={(event) => {
        if (!event.ctrlKey && !event.altKey && !event.metaKey && event.key !== "Escape")
          event.stopPropagation();
      }}
      onKeyUp={(event) => event.stopPropagation()}
      className="absolute inset-0 z-10 flex flex-col bg-panel text-fg @min-[72ch]:static @min-[72ch]:w-[min(calc(var(--spacing-sidebar)*1.25),60%)] @min-[72ch]:shrink-0 @min-[72ch]:border-l @min-[72ch]:border-line"
    >
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
