/** What a row lets its tree's keys do. */
export type TreeCan = { edit: boolean; remove: boolean; info: boolean; menu: boolean };

/** A row as the keyboard sees it, in the order the keys walk them. */
export type TreeKeyRow = {
  key: string;
  parent: string | null;
  /** Whether the rows under it show, `null` for a row with none. */
  expanded: boolean | null;
  can: TreeCan;
};

/** What a key does to the row the keyboard is on. */
export type TreeKeyEffect =
  | { move: number | "first" | "last" } // a place in the rows, which may be -1 or past the end
  | { fold: string; collapse: boolean }
  | { command: "open"; side: boolean }
  | { command: "edit" | "remove" | "info" | "menu" };

/**
 * The effect of a key pressed on a tree, or `null` when the tree leaves the key alone. With `chords`, Enter, F2 and
 * Delete work with any modifier and Ctrl+Enter opens to the side; without, every Ctrl, Alt or Meta key is left alone.
 */
export function treeKey(
  event: Pick<KeyboardEvent, "key" | "ctrlKey" | "altKey" | "metaKey" | "shiftKey">,
  rows: readonly TreeKeyRow[],
  at: number,
  { chords }: { chords: boolean },
): TreeKeyEffect | null {
  const modified = event.ctrlKey || event.altKey || event.metaKey;
  const from = Math.max(at, 0);
  const row = rows[from];
  if (!row || (modified && !chords)) return null;
  switch (event.key) {
    case "Enter":
      return { command: "open", side: event.ctrlKey };
    case "F2":
      return row.can.edit ? { command: "edit" } : null;
    case "Delete":
      return row.can.remove ? { command: "remove" } : null;
  }
  if (modified) return null;
  switch (event.key) {
    case "ArrowDown":
      return { move: at + 1 };
    case "ArrowUp":
      return { move: at < 0 ? 0 : at - 1 };
    case "Home":
      return { move: "first" };
    case "End":
      return { move: "last" };
    case "ArrowRight":
      if (row.expanded === null) return null;
      return row.expanded ? { move: from + 1 } : { fold: row.key, collapse: false };
    case "ArrowLeft": {
      if (row.expanded) return { fold: row.key, collapse: true };
      const parent = rows.findIndex(({ key }) => key === row.parent);
      return parent < 0 ? null : { move: parent };
    }
    case " ":
      return row.can.info ? { command: "info" } : null;
    case "ContextMenu":
      return row.can.menu ? { command: "menu" } : null;
    case "F10":
      return event.shiftKey && row.can.menu ? { command: "menu" } : null;
  }
  return null;
}
