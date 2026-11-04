/** The objects of the frame on screen as Frame scope lists them: found in the lane's drawn sample, searched and sorted here. */

import type { FileSortKey, FileSortWire } from "../generated/api";
import type { LaneFrame } from "../playback/contract";
import { filterRows } from "../present";
import { MOTION_STATES } from "../present/columns";
import { names, type SearchRecord } from "../search/match";

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
const UNKNOWN_CLASS = "unknown";

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

/** The keys a Frame object's search record can have, as the inspection names them. */
const KEY = {
  id: "id",
  class: "class",
  motion: "motion.state",
  score: "classification.score",
  confidence: "classification.confidence",
} as const;

/** Whether `path` names a field a Frame object's search record can carry; for any other, its absence is unknown. */
export const inRecord = (path: string): boolean =>
  Object.values(KEY).some((key) => names(path.toLowerCase(), key));

/** What a search looks at in an object: fields keyed as the inspection keys them, and its id, class and motion state as text. */
export function recordOf(object: FrameObject): SearchRecord {
  const { id, className, motion, score, confidence } = object;
  const fields: { [path: string]: (string | number)[] } = {
    [KEY.id]: [id],
    [KEY.class]: [className],
  };
  if (motion !== "") fields[KEY.motion] = [motion];
  if (score !== null) fields[KEY.score] = [score];
  if (confidence !== null) fields[KEY.confidence] = [confidence];
  return { fields, text: motion === "" ? [id, className] : [id, className, motion] };
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
