import {
  memo,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import { ContextMenu } from "../../kit/ContextMenu";
import { Icon } from "../../kit/Icon";
import type { MenuEntry } from "../../kit/Menu";
import { MiddleElided } from "../../kit/MiddleElided";
import { useRowWindow } from "../../shell/useRowWindow";
import { categoryDot } from "../category";
import { writeClipboard } from "./clipboard";
import { KINDS, rowText, type Column, type SortOf } from "./columns";
import { useLineFollow, type Follow } from "./lineFollow";
import { useColumns } from "./useColumns";

/** The grid every row and the header share; `--cols` is written by `useColumns`. */
const GRID = "grid grid-cols-[var(--cols,minmax(0,1fr))] items-center";
/** Where a row starts: the accent edge a selected row shows, then the padding before the first cell. */
const LEAD = "border-l-edge pl-m";
/** How long a copied row shows its check, in milliseconds. */
const COPIED_MS = 1500;
const MENU: readonly MenuEntry[] = [{ kind: "action", id: "copy", label: "Copy row" }];

/** On hover, gives the element `text` as its tooltip while it is cut short, and none otherwise. */
const tipIfCut = (text: string) => (event: MouseEvent<HTMLElement>) => {
  const element = event.currentTarget;
  element.title = element.scrollWidth > element.clientWidth ? text : "";
};

/**
 * One cell: text, an id elided in the middle, or what the column renders; a cut text shows in full as a tooltip. A
 * column with a `category` puts a dot in the class's color before the text.
 */
function Cell<Row>({ column, row }: { column: Column<Row>; row: Row }) {
  const kind = KINDS[column.kind];
  if (column.render || !column.text)
    return <div className="min-w-0 pr-m">{column.render?.(row)}</div>;
  const text = column.text(row);
  const category = column.category?.(row);
  const look = `${category === undefined ? "pr-m" : ""} ${column.muted ? "text-muted" : ""} ${kind.mono ? "font-mono text-aside" : ""} ${kind.numeric ? "text-right tabular-nums" : ""}`;
  const body =
    kind.elide === "middle" ? (
      <MiddleElided text={text} className={look} />
    ) : (
      <div onMouseEnter={tipIfCut(text)} className={`min-w-0 truncate ${look}`}>
        {text}
      </div>
    );
  if (category === undefined) return body;
  return (
    <div className="flex min-w-0 items-center gap-s pr-m">
      <span aria-hidden className={`size-m shrink-0 rounded-full ${categoryDot(category)}`} />
      {body}
    </div>
  );
}

/** What copying `row` puts on the clipboard: `copyText` where the table has one, else its columns' texts. */
function copyOf<Row>(
  columns: readonly Column<Row>[],
  copyText: ((row: Row) => string) | undefined,
  row: Row,
): string {
  return copyText ? copyText(row) : rowText(columns, row);
}

/** What a table keeps for the next time it is shown: how far it was scrolled and the key of the row its cursor was on. */
export type TableMemory = { top: number; cursor: string | null };

/**
 * The cursor: the row it follows, or, with no key, the position it was put on (the first row, or a row not loaded).
 * It is `waiting` when put on a row that is not loaded yet, and follows that row once it is; `open` when Enter was
 * pressed there, so the row is activated as it arrives, unless the list was replaced meanwhile.
 */
type Cursor = {
  key: string | null;
  index: number;
  waiting?: boolean;
  /** The `generation` Enter was pressed in. */
  open?: string;
};

type RowProps<Row> = {
  id: string;
  index: number;
  count: number;
  row: Row | undefined;
  columns: readonly Column<Row>[];
  shown: readonly number[];
  selected: boolean;
  active: boolean;
  dimmed: boolean;
  /** Whether the row is marked as the one the line is on. */
  tinted: boolean;
  /** Whether the row was just copied. */
  copied: boolean;
  cursor: string | undefined;
  onPick: (index: number) => void;
  onCopy: (index: number) => void;
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
  dimmed,
  tinted,
  copied,
  cursor,
  onPick,
  onCopy,
}: RowProps<Row>) {
  return (
    <div
      id={id}
      role="option"
      aria-selected={selected}
      aria-current={tinted ? "true" : undefined}
      aria-posinset={index + 1}
      aria-setsize={count}
      aria-busy={row === undefined ? true : undefined}
      data-cursor-id={cursor}
      onClick={() => onPick(index)}
      className={`${GRID} ${LEAD} group/row relative h-row cursor-pointer hover:bg-hover ${
        dimmed ? "text-muted [&>:not(button)]:opacity-50" : "text-fg"
      } ${selected ? "border-accent bg-hover" : "border-transparent"} ${tinted ? "bg-accent/15" : ""} ${
        active
          ? "group-focus-visible/list:outline-(length:--stroke-focus) group-focus-visible/list:-outline-offset-(--stroke-focus) group-focus-visible/list:outline-accent"
          : ""
      }`}
    >
      {row !== undefined &&
        shown.map((i) => <Cell key={columns[i].key} column={columns[i]} row={row} />)}
      {row !== undefined && (
        // The pointer's way to the copy that Ctrl+C and the context menu also do, which assistive technology uses.
        <button
          type="button"
          tabIndex={-1}
          aria-hidden
          title="Copy row"
          onClick={(event) => {
            event.stopPropagation();
            onCopy(index);
          }}
          className={`absolute inset-y-0 right-0 flex cursor-pointer items-center border-0 bg-hover px-m ${
            copied
              ? "text-accent"
              : "text-muted opacity-0 group-hover/row:opacity-100 hover:text-fg"
          }`}
        >
          <Icon name={copied ? "check" : "copy"} />
        </button>
      )}
      {copied && (
        <span role="status" className="sr-only">
          Copied
        </span>
      )}
    </div>
  );
}) as <Row>(props: RowProps<Row>) => ReactElement;

/**
 * A windowed, sortable list of rows in resizable columns, as a listbox. Columns give way to a narrow table (see
 * `fitColumns`) and the widths the user gives them are remembered for the session under `tableKey`. Only the rows in
 * view are drawn, each a memoized element without state. Ctrl+C and the row's context menu copy a row's text.
 */
export function Table<Row>({
  tableKey,
  label,
  columns,
  count,
  rowAt,
  keyOf,
  indexOf,
  selected,
  dimmed,
  tint,
  follow,
  copyText,
  generation = "",
  sort,
  onSort,
  onActivate,
  onRange,
  onCursor,
  cursorId,
  busy,
  message,
  memory,
}: {
  /** Names the table for the widths it remembers; a table of its own columns has its own key. */
  tableKey: string;
  label: string;
  columns: readonly Column<Row>[];
  count: number;
  /** The row at `index`, or `undefined` while it is not loaded. */
  rowAt: (index: number) => Row | undefined;
  keyOf: (row: Row) => string;
  /** The position of the row with this key in the current order, or -1 while it is not loaded or gone. */
  indexOf: (key: string) => number;
  /** The key of the selected row. */
  selected: string | null;
  /** Whether a row is dimmed. */
  dimmed?: (row: Row) => boolean;
  /** Whether a row is tinted: it is on the frame the line marks. */
  tint?: (row: Row) => boolean;
  /** A line before one of the rows, which the list scrolls to keep in view unless the user scrolls away from it. */
  follow?: Follow;
  /** What copying a row puts on the clipboard, instead of its columns' texts. */
  copyText?: (row: Row) => string;
  /** Names the list the rows are of (its query and order); an Enter still waiting for its row is dropped when it changes. */
  generation?: string;
  sort: SortOf<string>;
  onSort: (key: string) => void;
  /** A click, or Enter, on a row. */
  onActivate: (row: Row, index: number) => void;
  /** The rows in view changed. */
  onRange?: (start: number, end: number) => void;
  /** The cursor is on the row with this key. */
  onCursor?: (key: string) => void;
  /** The shared-pointer id of a row. */
  cursorId: (row: Row) => string;
  busy?: boolean;
  /** Shown instead of the rows. */
  message?: ReactNode;
  /** Written as the list scrolls and the cursor moves, and read once the rows first show. */
  memory?: TableMemory;
}) {
  const base = useId();
  // The cursor stays on its row as the order changes; once that row is not in the order the cursor is on no row.
  const [cursor, setCursor] = useState<Cursor>({ key: null, index: 0 });
  const { ref, start, end, update, reveal } = useRowWindow(count, '[role="option"]');
  const scroll = useLineFollow(ref, count, follow);
  const inView = Array.from({ length: end - start }, (_, i) => rowAt(start + i));
  const { root, header, shown, divider } = useColumns(tableKey, columns, inView, ref);

  useEffect(() => onRange?.(start, end), [onRange, start, end]);

  const latest = useRef({ rowAt, keyOf, onActivate, onCursor, memory, columns, copyText });
  useLayoutEffect(() => {
    latest.current = { rowAt, keyOf, onActivate, onCursor, memory, columns, copyText };
  });
  /** Puts the cursor on the row at `index`. */
  const place = useCallback((index: number) => {
    const { rowAt, keyOf, onCursor, memory } = latest.current;
    const row = rowAt(index);
    const key = row === undefined ? null : keyOf(row);
    setCursor({ key, index, waiting: row === undefined });
    if (memory) memory.cursor = key;
    if (key !== null) onCursor?.(key);
    return row;
  }, []);
  const pick = useCallback(
    (index: number) => {
      const row = place(index);
      if (row !== undefined) latest.current.onActivate(row, index);
    },
    [place],
  );

  /** The position of the cursor, or -1 when it is on no row. */
  const at =
    cursor.key === null ? Math.min(cursor.index, Math.max(count - 1, 0)) : indexOf(cursor.key);
  // The remembered scroll and cursor come back once, when there are rows to scroll to.
  const restored = useRef(false);
  useLayoutEffect(() => {
    if (restored.current || message != null || count === 0) return;
    restored.current = true;
    if (!memory) return;
    setCursor({ key: memory.cursor, index: 0 });
    scroll.scrollTo(memory.top);
    update();
  });

  // A cursor put on a row that was not loaded follows the row once it arrives.
  useLayoutEffect(() => {
    // An Enter from another generation is dropped for good, even if the list comes back to that one.
    if (cursor.open !== undefined && cursor.open !== generation)
      return setCursor({ ...cursor, open: undefined });
    if (!cursor.waiting) return;
    const row = rowAt(cursor.index);
    if (row === undefined) return;
    const key = keyOf(row);
    setCursor({ key, index: cursor.index });
    if (memory) memory.cursor = key;
    onCursor?.(key);
    if (cursor.open === generation) onActivate(row, cursor.index);
  });

  const go = (index: number) => {
    const next = Math.max(0, Math.min(count - 1, index));
    place(next);
    reveal(next);
  };
  const [copied, setCopied] = useState<string | null>(null);
  useEffect(() => {
    if (copied === null) return;
    const timer = setTimeout(() => setCopied(null), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);
  /** Copies the text of the row with `key`; the check shows once the clipboard has it. */
  const copy = useCallback(async (key: string, text: string) => {
    if (await writeClipboard(text)) setCopied(key);
  }, []);
  const copyRow = useCallback(
    (index: number) => {
      const { rowAt, keyOf, columns, copyText } = latest.current;
      const row = rowAt(index);
      if (row !== undefined) void copy(keyOf(row), copyOf(columns, copyText, row));
    },
    [copy],
  );
  // The row a context menu was opened on, as it was then: rows move while a video plays. A right click off the rows
  // leaves the browser's own menu.
  const [menuRow, setMenuRow] = useState<{ key: string; text: string } | null>(null);
  const onContextMenu = (event: MouseEvent) => {
    const row = (event.target as Element).closest('[role="option"]');
    const index = row ? Number(row.getAttribute("aria-posinset")) - 1 : -1;
    const found = index < 0 ? undefined : rowAt(index);
    if (found === undefined) event.stopPropagation();
    else setMenuRow({ key: keyOf(found), text: copyOf(columns, copyText, found) });
  };
  const onKeyDown = (event: KeyboardEvent) => {
    const page = end - start;
    const keys: Record<string, () => void> = {
      ArrowDown: () => go(at + 1),
      ArrowUp: () => go(at < 0 ? count - 1 : at - 1),
      PageDown: () => go(at + page),
      PageUp: () => go(at - page),
      Home: () => go(0),
      End: () => go(count - 1),
      // A row the cursor is on but that is not loaded is brought into view, to be chosen as it arrives.
      Enter: () => {
        if (at < 0) return;
        if (rowAt(at) !== undefined) return pick(at);
        setCursor({ key: null, index: at, waiting: true, open: generation });
        reveal(at);
      },
    };
    if (event.key === "c" && (event.ctrlKey || event.metaKey)) {
      const row = at < 0 ? undefined : rowAt(at);
      if (row !== undefined) void copy(keyOf(row), copyOf(columns, copyText, row));
      return;
    }
    keys[event.key]?.();
    if (event.key in keys) event.preventDefault();
  };

  return (
    <div ref={root} className="flex min-h-0 flex-1 flex-col">
      <div
        ref={header}
        className={`${GRID} ${LEAD} h-row shrink-0 overflow-hidden border-b border-transparent border-b-line text-small font-semibold tracking-caption text-muted uppercase [scrollbar-gutter:stable]`}
      >
        {shown.map((i, k) => {
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
              {k < shown.length - 1 && (
                <div
                  role="separator"
                  aria-orientation="vertical"
                  aria-label={`Resize ${column.title}`}
                  tabIndex={0}
                  {...divider(i)}
                  className="absolute inset-y-0 right-0 w-s cursor-col-resize after:absolute after:inset-y-s after:right-0 after:w-hairline after:bg-fg/15 hover:after:inset-y-xs hover:after:bg-fg/60 focus-ring"
                />
              )}
            </div>
          );
        })}
      </div>
      <div
        ref={ref}
        onScroll={(event) => {
          if (memory) memory.top = event.currentTarget.scrollTop;
          update();
          scroll.onScroll();
        }}
        className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto [scrollbar-gutter:stable]"
      >
        {message ?? (
          <ContextMenu
            entries={MENU}
            onSelect={() => menuRow && void copy(menuRow.key, menuRow.text)}
          >
            <div
              role="listbox"
              aria-label={label}
              aria-activedescendant={at < start || at >= end ? undefined : `${base}-${at}`}
              aria-busy={busy}
              tabIndex={0}
              onKeyDown={onKeyDown}
              onContextMenu={onContextMenu}
              className="group/list relative focus:outline-none"
              style={{
                paddingTop: `calc(var(--spacing-row) * ${start})`,
                paddingBottom: `calc(var(--spacing-row) * ${count - end})`,
              }}
            >
              {follow?.line != null && (
                <div
                  aria-hidden
                  data-now=""
                  className="pointer-events-none absolute inset-x-0 z-10 h-xs bg-accent"
                  style={{
                    top: `calc(var(--spacing-row) * ${follow.line} - var(--spacing-xs) / 2)`,
                  }}
                />
              )}
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
                    dimmed={row !== undefined && dimmed?.(row) === true}
                    tinted={row !== undefined && tint?.(row) === true}
                    copied={row !== undefined && copied === keyOf(row)}
                    cursor={row === undefined ? undefined : cursorId(row)}
                    onPick={pick}
                    onCopy={copyRow}
                  />
                );
              })}
            </div>
          </ContextMenu>
        )}
      </div>
    </div>
  );
}
