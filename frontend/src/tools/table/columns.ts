import type { ReactNode } from "react";

/** How a column's cells look and size: ids elide in the middle, numbers sit at the end, a presence strip has no text. */
export type CellKind = "id" | "text" | "number" | "presence";

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
    comfortable: number;
    least: number;
  }
> = {
  id: { align: "start", elide: "middle", numeric: false, comfortable: 20, least: 10 },
  text: { align: "start", elide: "end", numeric: false, comfortable: 10, least: 8 },
  number: { align: "end", elide: "end", numeric: true, comfortable: 6, least: 4 },
  presence: { align: "start", elide: "end", numeric: false, comfortable: 12, least: 4 },
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

/** The sort after a click on the header of `key`: the same column flips; a number column starts greatest first. */
export function nextSort<Key extends string>(
  columns: readonly Column<never>[],
  sort: SortOf<Key>,
  key: Key,
): SortOf<Key> {
  const kind = columns.find((column) => column.key === key)?.kind;
  return { key, descending: sort.key === key ? !sort.descending : kind === "number" };
}
