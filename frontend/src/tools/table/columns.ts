import type { ReactNode } from "react";

/**
 * How a column's cells look and size: ids elide in the middle, numbers sit at the end, ids, numbers and `mono` text are
 * monospace, a presence strip has no text.
 */
export type CellKind = "id" | "text" | "mono" | "number" | "presence";

/**
 * What each kind decides. `comfortable` and `least` are characters: the width a column keeps while other columns
 * can still be hidden, and the narrowest it is ever shrunk to (less if its values are shorter).
 */
export const KINDS: Record<
  CellKind,
  {
    align: "start" | "end";
    elide: "end" | "middle";
    numeric: boolean;
    mono: boolean;
    comfortable: number;
    least: number;
  }
> = {
  id: { align: "start", elide: "middle", numeric: false, mono: true, comfortable: 20, least: 10 },
  text: { align: "start", elide: "end", numeric: false, mono: false, comfortable: 10, least: 8 },
  mono: { align: "start", elide: "end", numeric: false, mono: true, comfortable: 10, least: 8 },
  number: { align: "end", elide: "end", numeric: true, mono: true, comfortable: 6, least: 4 },
  presence: {
    align: "start",
    elide: "end",
    numeric: false,
    mono: false,
    comfortable: 12,
    least: 4,
  },
};

/** One column of a table: its header, how its cells look and how it gives way when the table is narrow. */
export interface Column<Row> {
  /** Identifies the column to `onSort`. */
  key: string;
  /** The header text; remembered widths are filed under it. */
  title: string;
  kind: CellKind;
  /** The cell's text: measured, searched for emptiness, shown in the tooltip. Kinds without text omit it. */
  text?: (row: Row) => string;
  /** The class a cell's dot takes its color from; the cell draws the dot before its text. */
  category?: (row: Row) => string;
  /** The cell's content for a kind that is not text. */
  render?: (row: Row) => ReactNode;
  /** Whether the header sorts by this column. */
  sortable?: boolean;
  /** 0 keeps the column shown; a higher number is hidden earlier, the highest first, once it does not fit. */
  priority?: number;
  /** Takes the room the others leave. */
  stretch?: boolean;
  /** Fewest characters the column is shrunk to, instead of its kind's. */
  least?: number;
}

/** A sort and its direction. */
export type SortOf<Key extends string> = { key: Key; descending: boolean };

/** The sort after a click on the header of `key`: the same column flips; another starts ascending. */
export const nextSort = <Key extends string>(sort: SortOf<Key>, key: Key): SortOf<Key> => ({
  key,
  descending: sort.key === key ? !sort.descending : false,
});

/** What copying a row puts on the clipboard: the text of every column that has text, hidden ones too, tab-separated. */
export const rowText = <Row>(columns: readonly Column<Row>[], row: Row): string =>
  columns.flatMap((column) => (column.text ? [column.text(row)] : [])).join("\t");
