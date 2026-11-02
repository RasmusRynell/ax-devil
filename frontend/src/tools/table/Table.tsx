import {
  memo,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import { Icon } from "../../kit/Icon";
import { useRowWindow } from "../../shell/useRowWindow";
import { KINDS, type Column, type SortOf } from "./columns";
import { useColumns } from "./useColumns";

/** The grid every row and the header share; `--cols` is written by `useColumns`. */
const GRID = "grid grid-cols-[var(--cols,minmax(0,1fr))] items-center";
/** Where a row starts: the accent edge a selected row shows, then the padding before the first cell. */
const LEAD = "border-l-edge pl-m";
/** The characters of an elided id that always show. */
const TAIL = 5;

/** One cell: text with a tooltip, an id elided in the middle, or what the column renders. */
function Cell<Row>({ column, row }: { column: Column<Row>; row: Row }) {
  const kind = KINDS[column.kind];
  if (!column.text) return <div className="min-w-0 pr-m">{column.render?.(row)}</div>;
  const text = column.text(row);
  if (kind.elide === "middle" && text.length > 2 * TAIL)
    return (
      <div title={text} className="flex min-w-0 pr-m">
        <span className="truncate">{text.slice(0, -TAIL)}</span>
        <span className="shrink-0">{text.slice(-TAIL)}</span>
      </div>
    );
  return (
    <div
      title={text}
      className={`min-w-0 truncate pr-m ${kind.numeric ? "text-right tabular-nums" : ""}`}
    >
      {text}
    </div>
  );
}

type RowProps<Row> = {
  id: string;
  index: number;
  count: number;
  row: Row | undefined;
  columns: readonly Column<Row>[];
  shown: readonly number[];
  selected: boolean;
  active: boolean;
  /** Why the row is dimmed, when it is. */
  absent: string | undefined;
  cursor: string | undefined;
  onPick: (index: number) => void;
};

/** One row. It holds no state and no hooks, and is drawn again only when its own props change. */
const TableRow = memo(function TableRow<Row>({
  id,
  index,
  count,
  row,
  columns,
  shown,
  selected,
  active,
  absent,
  cursor,
  onPick,
}: RowProps<Row>) {
  return (
    <div
      id={id}
      role="option"
      aria-selected={selected}
      aria-posinset={index + 1}
      aria-setsize={count}
      aria-busy={row === undefined ? true : undefined}
      title={absent}
      data-cursor-id={cursor}
      onClick={() => onPick(index)}
      className={`${GRID} ${LEAD} h-row cursor-pointer hover:bg-hover ${
        absent ? "text-muted" : "text-fg"
      } ${selected ? "border-accent bg-hover" : "border-transparent"} ${
        active
          ? "group-focus-visible/list:outline-(length:--stroke-focus) group-focus-visible/list:-outline-offset-(--stroke-focus) group-focus-visible/list:outline-accent"
          : ""
      }`}
    >
      {row !== undefined &&
        shown.map((i) => <Cell key={columns[i].key} column={columns[i]} row={row} />)}
    </div>
  );
}) as <Row>(props: RowProps<Row>) => ReactElement;

/**
 * A windowed, sortable list of rows in resizable columns, as a listbox. Columns give way to a narrow table (see
 * `fitColumns`) and the widths the user gives them are remembered for the session under `tableKey`. Only the rows in
 * view are drawn, each a memoized element without state.
 */
export function Table<Row>({
  tableKey,
  label,
  columns,
  count,
  rowAt,
  keyOf,
  selected,
  absent,
  sort,
  onSort,
  onActivate,
  onRange,
  cursorId,
  busy,
  message,
}: {
  /** Names the table for the widths it remembers; a table of its own columns has its own key. */
  tableKey: string;
  label: string;
  columns: readonly Column<Row>[];
  count: number;
  /** The row at `index`, or `undefined` while it is not loaded. */
  rowAt: (index: number) => Row | undefined;
  keyOf: (row: Row) => string;
  /** The key of the selected row. */
  selected: string | null;
  /** Why a row is dimmed; `undefined` for one that is not. */
  absent?: (row: Row) => string | undefined;
  sort: SortOf<string>;
  onSort: (key: string) => void;
  /** A click, or Enter or Space, on a row. */
  onActivate: (row: Row, index: number) => void;
  /** The rows in view changed. */
  onRange?: (start: number, end: number) => void;
  /** The shared-pointer id of a row. */
  cursorId: (row: Row) => string;
  busy?: boolean;
  /** Shown instead of the rows. */
  message?: ReactNode;
}) {
  const base = useId();
  const [active, setActive] = useState(0);
  const { ref, start, end, update, reveal } = useRowWindow(count, '[role="option"]');
  const inView = Array.from({ length: end - start }, (_, i) => rowAt(start + i));
  const { root, header, shown, divider } = useColumns(tableKey, columns, inView, ref);

  useEffect(() => onRange?.(start, end), [onRange, start, end]);

  const latest = useRef({ rowAt, onActivate });
  useLayoutEffect(() => {
    latest.current = { rowAt, onActivate };
  });
  const pick = useCallback((index: number) => {
    setActive(index);
    const row = latest.current.rowAt(index);
    if (row !== undefined) latest.current.onActivate(row, index);
  }, []);

  const at = Math.min(active, Math.max(count - 1, 0));
  const go = (index: number) => {
    const next = Math.max(0, Math.min(count - 1, index));
    setActive(next);
    reveal(next);
  };
  const onKeyDown = (event: KeyboardEvent) => {
    const page = end - start;
    const keys: Record<string, () => void> = {
      ArrowDown: () => go(at + 1),
      ArrowUp: () => go(at - 1),
      PageDown: () => go(at + page),
      PageUp: () => go(at - page),
      Home: () => go(0),
      End: () => go(count - 1),
      Enter: () => pick(at),
    };
    keys[event.key === " " ? "Enter" : event.key]?.();
    if (event.key in keys || event.key === " ") event.preventDefault();
  };

  return (
    <div ref={root} className="flex min-h-0 flex-1 flex-col">
      <div
        ref={header}
        className={`${GRID} ${LEAD} h-row shrink-0 overflow-hidden border-transparent text-small text-muted [scrollbar-gutter:stable]`}
      >
        {shown.map((i) => {
          const column = columns[i];
          const on = sort.key === column.key;
          return (
            <div
              key={column.key}
              className={`relative flex min-w-0 pr-m ${KINDS[column.kind].align === "end" ? "justify-end" : ""}`}
            >
              {column.sortable ? (
                <button
                  type="button"
                  aria-label={`Sort by ${column.title}${on ? (sort.descending ? ", descending" : ", ascending") : ""}`}
                  aria-pressed={on}
                  onClick={() => onSort(column.key)}
                  className={`flex min-w-0 items-center gap-xs rounded-control bg-transparent p-0 hover:text-fg focus-ring ${
                    on ? "text-fg" : ""
                  }`}
                >
                  <span className="truncate">{column.title}</span>
                  {on && (
                    <span aria-hidden className={`flex ${sort.descending ? "" : "rotate-180"}`}>
                      <Icon name="chevron-down" />
                    </span>
                  )}
                </button>
              ) : (
                <span className="truncate">{column.title}</span>
              )}
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label={`Resize ${column.title}`}
                tabIndex={0}
                {...divider(i)}
                className="absolute inset-y-0 right-0 w-s cursor-col-resize hover:bg-line-strong focus-ring"
              />
            </div>
          );
        })}
      </div>
      <div
        ref={ref}
        onScroll={update}
        className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto [scrollbar-gutter:stable]"
      >
        {message ?? (
          <div
            role="listbox"
            aria-label={label}
            aria-activedescendant={`${base}-${at}`}
            aria-busy={busy}
            tabIndex={0}
            onKeyDown={onKeyDown}
            className="group/list focus:outline-none"
            style={{
              paddingTop: `calc(var(--spacing-row) * ${start})`,
              paddingBottom: `calc(var(--spacing-row) * ${count - end})`,
            }}
          >
            {inView.map((row, i) => {
              const index = start + i;
              return (
                <TableRow
                  key={index}
                  id={`${base}-${index}`}
                  index={index}
                  count={count}
                  row={row}
                  columns={columns}
                  shown={shown}
                  selected={row !== undefined && keyOf(row) === selected}
                  active={index === at}
                  absent={row === undefined ? undefined : absent?.(row)}
                  cursor={row === undefined ? undefined : cursorId(row)}
                  onPick={pick}
                />
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
