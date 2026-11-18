import { useCallback, useRef, useState, type ReactNode } from "react";
import { Menu, type MenuCommand, type MenuEntry } from "../../kit/Menu";
import { Popup } from "../../kit/Popup";

const NOTHING_CHECKED: ReadonlySet<string> = new Set();

/**
 * The context menu of a tree's rows. `open` shows the menu of the row with a key at a point, remembering the tree it
 * came from, which takes the focus back when the menu goes. The row is looked up by its key on every render, so an
 * open menu follows the row's updates and goes with the row; a row with no entries draws no menu.
 */
export function useTreeMenu<S>(
  find: (key: string) => S | undefined,
  entriesOf: (row: S) => MenuEntry[],
  choose: (row: S, id: string) => void,
): {
  open: (key: string, x: number, y: number, tree: HTMLElement | null) => void;
  popup: ReactNode;
} {
  const [at, setAt] = useState<{ key: string; x: number; y: number } | null>(null);
  const from = useRef<HTMLElement | null>(null);
  const close = useCallback(() => {
    setAt(null);
    from.current?.focus({ preventScroll: true });
  }, []);
  const open = useCallback((key: string, x: number, y: number, tree: HTMLElement | null) => {
    from.current = tree;
    setAt({ key, x, y });
  }, []);

  const row = at && find(at.key);
  const entries = row ? entriesOf(row) : [];
  const popup = at && row && entries.length > 0 && (
    <Popup at={at} onClose={close} className="z-20 min-w-max">
      <Menu
        entries={entries}
        checked={NOTHING_CHECKED}
        onSelect={(command: MenuCommand) => {
          close();
          choose(row, command.id);
        }}
      />
    </Popup>
  );
  return { open, popup };
}
