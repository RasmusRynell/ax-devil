import type { FileRowWire } from "../generated/api";
import { CodePoints, codePointString } from "../present/expr/codePoints";
import { emitFixed } from "../present/expr/fixed";
import { categoryDot } from "./category";
import { UNKNOWN_CLASS, type FrameObject } from "./objects";
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
          className={`absolute inset-y-0 min-w-hairline ${categoryDot(categoryOf(row))}`}
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
export const fileColumns = (
  frames: number,
  seek?: (frame: number) => void,
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
