/** The objects of the frame on screen as Frame scope lists them: found in the lane's drawn sample, searched and sorted here. */

import type { FileSortKey, FileSortWire, SearchWire } from "../generated/api";
import type { LaneFrame } from "../playback/contract";
import type { FrameLeaves } from "../playback/frameFields";
import { filterRows } from "../present";
import { MOTION_STATES } from "../present/columns";
import type { SearchRecord } from "../search/match";

/** One object of the frame on screen. */
export type FrameObject = {
  id: string;
  className: string;
  /** `moving`, `stationary`, `unknown` or `""`. */
  motion: string;
  score: number | null;
  confidence: number | null;
};

/** What a list is sorted by: both scopes offer `id` and `class`, Frame scope the rest but `first_seen`, File scope that. */
export type SortKey = FileSortKey | "score" | "confidence" | "motion";

/** A sort and its direction. */
export type Sort = { key: SortKey; descending: boolean };

const FILE_KEYS: ReadonlySet<SortKey> = new Set<FileSortKey>(["id", "class", "first_seen"]);

/** Whether the backend can sort by `sort`: all of File scope's can. */
export const isFileSort = (sort: Sort): sort is FileSortWire => FILE_KEYS.has(sort.key);

const number = (value: number): number | null => (Number.isNaN(value) ? null : value);

/** What an object with no class is called. */
export const UNKNOWN_CLASS = "unknown";

/** The id of every object `frame` drew, before the lane's class toggles and filter; none when the lane drew nothing. */
export function drawnIds(frame: LaneFrame | undefined): Set<string> {
  const ids = new Set<string>();
  if (!frame) return ids;
  const { columns } = frame;
  for (let row = 0; row < columns.rows; row++)
    ids.add(columns.strings.string(columns.entityId[row]));
  return ids;
}

/** The objects `frame` shows after the lane's class toggles and frame filter; none when the lane drew nothing. */
export function frameObjects(frame: LaneFrame | undefined): FrameObject[] {
  if (!frame) return [];
  const { columns } = frame;
  const { strings } = columns;
  const kept = filterRows(columns, frame.filter, frame.classMask);
  // Decoding a string allocates, and a frame has a handful of classes for thousands of objects.
  const classes = new Map<number, string>();
  const classOf = (id: number): string => {
    let name = classes.get(id);
    if (name === undefined) classes.set(id, (name = strings.string(id)));
    return name;
  };
  return Array.from(kept.rows.subarray(0, kept.count), (row) => ({
    id: strings.string(columns.entityId[row]),
    className: columns.classId[row] < 0 ? UNKNOWN_CLASS : classOf(columns.classId[row]),
    motion: columns.motion[row] === 0 ? "" : MOTION_STATES[columns.motion[row] - 1],
    score: number(columns.score[row]),
    confidence: number(columns.confidence[row]),
  }));
}

/** The paths every Frame object has whatever the search, so that a search naming only these asks the backend nothing. */
const LIGHT_PATHS: ReadonlySet<string> = new Set(["id", "class"]);

/**
 * The field paths `search` names that need the object's inspected leaves (`debug.vendor.id`, `score`, `rgb.r`), in
 * lower case without repeats; none when it names only `id` and `class`.
 */
export function inspectedPaths(search: SearchWire): string[] {
  const paths = search.groups
    .flat()
    .flatMap(({ path }) => (path === null ? [] : [path.toLowerCase()]));
  return [...new Set(paths)].filter((path) => !LIGHT_PATHS.has(path));
}

/**
 * What a search looks at in an object: its id and class, then the leaves the backend read at the paths the search
 * names (`leaves`, keyed by `group.path`); and its id, class and motion state as text.
 */
export function recordOf(object: FrameObject, leaves?: FrameLeaves | null): SearchRecord {
  const { id, className, motion } = object;
  return {
    fields: { ...leaves?.get(id), id: [id], class: [className] },
    text: motion === "" ? [id, className] : [id, className, motion],
  };
}

/** Ids order with digit runs by value, so `id9` comes before `id10`. */
const byId = new Intl.Collator(undefined, { numeric: true }).compare;

const idOf = (object: FrameObject): string => object.id;

/** What Frame scope sorts by for each key but `id`; a missing number is the lowest. */
const FRAME_KEYS: Partial<Record<SortKey, (object: FrameObject) => string | number>> = {
  class: (object) => object.className.toLowerCase(),
  score: (object) => object.score ?? -Infinity,
  confidence: (object) => object.confidence ?? -Infinity,
  motion: (object) => object.motion,
};

/** An object with the key it sorts by. */
interface Keyed {
  key: string | number;
  object: FrameObject;
}

/** `objects` in order; objects equal in the key are in ascending id order whichever way the key goes. */
export function sortObjects(
  objects: readonly FrameObject[],
  { key, descending }: Sort,
): FrameObject[] {
  // Each key is computed once, not on every comparison.
  const keyOf = key === "id" ? idOf : (FRAME_KEYS[key] ?? idOf);
  const rows: Keyed[] = objects.map((object) => ({ key: keyOf(object), object }));
  const flip = descending ? -1 : 1;
  const byKey =
    key === "id"
      ? (a: Keyed, b: Keyed) => byId(a.object.id, b.object.id)
      : (a: Keyed, b: Keyed) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
  rows.sort((a, b) => byKey(a, b) * flip || byId(a.object.id, b.object.id));
  return rows.map((row) => row.object);
}
