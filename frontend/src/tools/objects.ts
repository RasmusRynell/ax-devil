/** The objects of the frame on screen as Frame scope lists them: found in the lane's drawn sample, searched and sorted here. */

import type { FileSortKey, FileSortWire } from "../generated/api";
import type { LaneFrame } from "../playback/contract";
import { filterRows } from "../present";
import { MOTION_STATES } from "../present/columns";
import type { SearchRecord } from "../search/match";

/** One object of the frame on screen. */
type FrameObject = {
  id: string;
  className: string;
  /** `moving`, `stationary`, `unknown` or `""`. */
  motion: string;
  score: number | null;
  confidence: number | null;
};

/** What a list is sorted by: Frame scope offers `score`, File scope `first_seen`. */
export type SortKey = FileSortKey | "score";

/** A sort and its direction. */
export type Sort = { key: SortKey; descending: boolean };

/** Whether the backend can sort by `sort`: all of File scope's can. */
export const isFileSort = (sort: Sort): sort is FileSortWire => sort.key !== "score";

const number = (value: number): number | null => (Number.isNaN(value) ? null : value);

/** The objects `frame` shows after the lane's class toggles and frame filter; none when the lane drew nothing. */
export function frameObjects(frame: LaneFrame | undefined): FrameObject[] {
  if (!frame) return [];
  const { columns } = frame;
  const { strings } = columns;
  const kept = filterRows(columns, frame.filter, frame.classMask);
  return Array.from(kept.rows.subarray(0, kept.count), (row) => ({
    id: strings.string(columns.entityId[row]),
    className: columns.classId[row] < 0 ? "" : strings.string(columns.classId[row]),
    motion: columns.motion[row] === 0 ? "" : MOTION_STATES[columns.motion[row] - 1],
    score: number(columns.score[row]),
    confidence: number(columns.confidence[row]),
  }));
}

/** What a search looks at in an object: its id, class, motion, score and confidence. */
export function recordOf(object: FrameObject): SearchRecord {
  const { id, className, motion, score, confidence } = object;
  const fields: { [path: string]: (string | number)[] } = {
    id: [id],
    class: [className],
    motion: [motion],
  };
  if (score !== null) fields.score = [score];
  if (confidence !== null) fields.confidence = [confidence];
  return { fields, text: [id, className, motion] };
}

/** Ids order with digit runs by value, so `id9` comes before `id10`. */
const byId = new Intl.Collator(undefined, { numeric: true }).compare;

/** `objects` in order; objects equal in the key are in ascending id order whichever way the key goes. */
export function sortObjects(
  objects: readonly FrameObject[],
  { key, descending }: Sort,
): FrameObject[] {
  const keyed = (object: FrameObject): string | number =>
    key === "class"
      ? object.className.toLowerCase()
      : key === "score"
        ? (object.score ?? -Infinity)
        : object.id;
  const flip = descending ? -1 : 1;
  return [...objects].sort((a, b) => {
    const [x, y] = [keyed(a), keyed(b)];
    const order = key === "id" ? byId(a.id, b.id) : x < y ? -1 : x > y ? 1 : 0;
    return order * flip || byId(a.id, b.id);
  });
}
