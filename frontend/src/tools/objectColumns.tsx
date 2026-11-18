import { createContext, useContext } from "react";
import type { FileRowWire } from "../generated/api";
import { CodePoints, codePointString } from "../present/expr/codePoints";
import { emitFixed } from "../present/expr/fixed";
import { UNKNOWN_CLASS, type FrameObject } from "./objects";
import { PresenceStrip } from "./PresenceStrip";
import type { Column } from "./table/columns";

const digits = new CodePoints();
/** A score or confidence to two decimals, rounded as Python prints it; blank when there is none. */
const score = (value: number | null): string => {
  digits.clear();
  return value !== null && emitFixed(value, 2, digits)
    ? codePointString(digits.data, 0, digits.length)
    : "";
};

/** What Frame scope lists of an object; the first three are required, Motion goes first when the panel is narrow. */
export const FRAME_COLUMNS: readonly Column<FrameObject>[] = [
  {
    key: "id",
    title: "ID",
    kind: "id",
    text: (object) => object.id,
    category: (object) => object.className,
    sortable: true,
  },
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

/** The class that colors a File row, the first it had. */
const categoryOf = (row: FileRowWire): string => row.classes[0] ?? UNKNOWN_CLASS;

/** A frame time in microseconds as `m:ss`, or `h:mm:ss` from one hour, the whole seconds of its UTC day. */
export function shortTime(us: number): string {
  const seconds = Math.floor(us / 1_000_000);
  const hours = Math.floor(seconds / 3600) % 24;
  const minutes = Math.floor(seconds / 60) % 60;
  const rest = String(seconds % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

/** The frame on screen, marked on each File row's strip. A context, so that a new frame draws the strips and not the table. */
export const ShownFrame = createContext<number | undefined>(undefined);

/** Where an object is across the video, with the frame on screen marked. */
function Presence({ row, frames }: { row: FileRowWire; frames: number }) {
  const current = useContext(ShownFrame);
  return (
    <PresenceStrip
      spans={row.presence}
      frames={frames}
      category={categoryOf(row)}
      current={current}
    />
  );
}

/** What File scope lists of an object: where it is across the video, and the times of the frames that spans. */
export const fileColumns = (
  frames: number,
  timeOf: (frame: number) => number | undefined,
): Column<FileRowWire>[] => [
  {
    key: "id",
    title: "ID",
    kind: "id",
    text: (row) => row.id,
    category: categoryOf,
    sortable: true,
  },
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
    render: (row) => <Presence row={row} frames={frames} />,
    stretch: true,
  },
  {
    key: "first_seen",
    title: "Seen",
    kind: "mono",
    text: (row) => {
      const first = timeOf(row.first_frame);
      const last = timeOf(row.last_frame);
      return first === undefined || last === undefined
        ? ""
        : `${shortTime(first)}–${shortTime(last)}`;
    },
    sortable: true,
    priority: 1,
  },
];
