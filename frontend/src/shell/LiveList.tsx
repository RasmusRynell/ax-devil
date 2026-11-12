import { useState, type KeyboardEvent, type RefObject } from "react";
import type { Row, RowAction } from "../live/rows";
import { LiveRow } from "../live/LiveRow";
import { useSpanWindow } from "./useSpanWindow";

/** A row shown in a section's tree, in the order the keyboard walks them. */
export type Listed = {
  row: Row;
  level: 1 | 2;
  parent: string | null;
  /** Whether the streams under it show, `null` for a row with none. */
  expanded: boolean | null;
  /** Its place among the rows that share its parent, from 1, and how many they are. */
  position: number;
  siblings: number;
};

interface Props {
  label: string;
  /** Makes the ids of the rows unique among the trees of the page. */
  prefix: string;
  rows: readonly Listed[];
  /** The scrolling element that holds the tree, which draws only the rows in view. */
  scroller: RefObject<HTMLElement | null>;
  /** The row whose info lines a click opened. */
  info: string | null;
  mayAct: boolean;
  onInfo: (row: Row) => void;
  /** Enter or a double click: start the stream, or bring its viewer to the front. */
  onOpen: (row: Row) => void;
  onRemove: (row: Row) => void;
  onFold: (row: Row) => void;
  /** The menu of a row at a point, and the tree to give the focus back to when it closes. */
  onMenu: (row: Row, x: number, y: number, tree: HTMLElement | null) => void;
  /** The pointer entered a row (where), or left it. */
  onHover: (row: Row, at: { x: number; y: number } | null) => void;
  onAct: (row: Row, action: RowAction) => void;
}

/**
 * One section's rows as a tree that draws only the rows in view, however many a scan lists. The arrow keys, Home and
 * End move between the rows, left and right fold and unfold, Enter opens, Space shows the info lines, F2 edits,
 * Delete removes and the menu key or Shift+F10 opens the row's menu. Keys that reach it from a button inside a row are the
 * button's. Focusing the tree puts the keyboard on its first row.
 */
export function LiveList({
  label,
  prefix,
  rows,
  scroller,
  info,
  mayAct,
  onInfo,
  onOpen,
  onRemove,
  onFold,
  onMenu,
  onHover,
  onAct,
}: Props) {
  const [current, setCurrent] = useState<string | null>(null);
  const { ref, start, end, before, after, reveal } = useSpanWindow(
    rows.map(({ row }) => row.id),
    scroller,
  );
  const at = rows.findIndex(({ row }) => row.id === current);
  const idOf = (row: Row) => `${prefix}-${row.id}`;

  const go = (index: number) => {
    const clamped = Math.max(0, Math.min(rows.length - 1, index));
    const next = rows[clamped];
    if (!next) return;
    setCurrent(next.row.id);
    reveal(clamped);
  };
  const menuAt = (row: Row, x: number, y: number) => onMenu(row, x, y, ref.current);

  const keys = (event: KeyboardEvent<HTMLDivElement>) => {
    // A key meant for a button inside a row, such as Enter on its action, is the button's.
    if (event.target !== event.currentTarget) return;
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    const here = rows[Math.max(0, at)];
    if (!here) return;
    const { row } = here;
    if (event.key === "ArrowDown") go(at + 1);
    else if (event.key === "ArrowUp") go(at - 1);
    else if (event.key === "Home") go(0);
    else if (event.key === "End") go(rows.length - 1);
    else if (event.key === "ArrowRight" && here.expanded === false) onFold(row);
    else if (event.key === "ArrowRight" && here.expanded) go(at + 1);
    else if (event.key === "ArrowLeft" && here.expanded) onFold(row);
    else if (event.key === "ArrowLeft" && here.parent !== null)
      go(rows.findIndex((other) => other.row.id === here.parent));
    else if (event.key === "Enter") onOpen(row);
    else if (event.key === " ") onInfo(row);
    else if (event.key === "Delete") onRemove(row);
    else if (event.key === "F2" && mayAct && row.menu.includes("edit")) onAct(row, "edit");
    else if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
      if (row.menu.length === 0) return;
      const box = document.getElementById(idOf(row))?.getBoundingClientRect();
      menuAt(row, box?.left ?? 0, box?.bottom ?? 0);
    } else return;
    event.preventDefault();
  };

  const drawn = rows.slice(start, end);
  const activeRow = at >= start && at < end ? rows[at]?.row : undefined;
  return (
    <div
      ref={ref}
      role="tree"
      aria-label={label}
      aria-activedescendant={activeRow ? idOf(activeRow) : undefined}
      tabIndex={0}
      onKeyDown={keys}
      onFocus={(event) => {
        // The keyboard starts on the first row, so the first arrow key moves on from it.
        const first = rows[0];
        if (event.target === event.currentTarget && at < 0 && first) setCurrent(first.row.id);
      }}
      // The rows left out stand in as padding, which only their measured heights can give.
      style={{ paddingTop: before, paddingBottom: after }}
      className="group/tree flex flex-col focus:outline-none"
    >
      {drawn.map(({ row, level, expanded, position, siblings }) => (
        <LiveRow
          key={row.id}
          row={row}
          id={idOf(row)}
          level={level}
          expanded={expanded}
          position={position}
          siblings={siblings}
          current={row.id === current}
          infoShown={row.id === info}
          mayAct={mayAct}
          onClick={() => {
            setCurrent(row.id);
            ref.current?.focus({ preventScroll: true });
            onInfo(row);
          }}
          onOpen={() => onOpen(row)}
          onFold={() => onFold(row)}
          onMenu={(x, y) => {
            setCurrent(row.id);
            menuAt(row, x, y);
          }}
          onHover={(point) => onHover(row, point)}
          onAct={(action) => onAct(row, action)}
        />
      ))}
    </div>
  );
}
