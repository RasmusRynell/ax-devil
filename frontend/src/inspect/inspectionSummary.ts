import { MOTION_STATES } from "../present/columns";
import { maskKeeps } from "../present/filter/filter";
import type { InspectionView, LaneFrame, Summary } from "../lanes/contract";

const encoder = new TextEncoder();

/** The sample a selection is looked up in: the frame its lane draws, or why the object is not shown in it. */
export type Shown = { frame: LaneFrame; summary: Summary } | "gone" | "filtered";

/** Stable sample/entity identity used by inspection ordering. */
export const keyOf = (source: number, revision: number, sample: number, entity: string): string =>
  `${source}:${revision}:${sample}:${entity}`;

/**
 * What the Sample Item says of the object; null when it has no such object, "filtered" when the lane's class toggles
 * or frame filter keep it from being shown (the same test the lane's rows are kept by).
 */
export function summarize(frame: LaneFrame, entity: string): Summary | "filtered" | null {
  const { columns } = frame;
  const bytes = encoder.encode(entity);
  for (let row = 0; row < columns.rows; row++) {
    if (!columns.strings.equals(columns.entityId[row], bytes)) continue;
    const test = frame.filter?.test ?? null;
    frame.filter?.ctx?.bind(columns);
    if (
      !maskKeeps(columns.classMask, row * columns.maskWords, frame.classMask) ||
      (test !== null && test(row) !== 1)
    )
      return "filtered";
    const classId = columns.classId[row];
    const motion = columns.motion[row];
    const number = (value: number) => (Number.isNaN(value) ? null : value);
    return {
      entity,
      className: classId < 0 ? null : columns.strings.string(classId),
      score: number(columns.score[row]),
      confidence: number(columns.confidence[row]),
      motion: motion === 0 ? null : MOTION_STATES[motion - 1],
    };
  }
  return null;
}

/** Whether a summary still describes the same displayed object. */
export const sameSummary = (a: Summary, b: Summary): boolean =>
  a.entity === b.entity &&
  a.className === b.className &&
  a.score === b.score &&
  a.confidence === b.confidence &&
  a.motion === b.motion;

/** Whether an inspection view already shows these fields, pins and transfer state. */
export function sameInspection(before: InspectionView, next: InspectionView): boolean {
  return (
    before.state === next.state &&
    (before.state === "none" ||
      (next.state !== "none" &&
        before.selection.lane === next.selection.lane &&
        before.selection.source === next.selection.source &&
        before.selection.entity === next.selection.entity &&
        (before.state === "absent"
          ? next.state === "absent" &&
            before.reason === next.reason &&
            (before.last === next.last ||
              (before.last !== null && next.last !== null && sameSummary(before.last, next.last)))
          : next.state !== "absent" &&
            sameSummary(before.summary, next.summary) &&
            before.fields === next.fields &&
            before.pins === next.pins &&
            before.error === next.error &&
            before.truncated === next.truncated &&
            before.access?.revision === next.access?.revision)))
  );
}
