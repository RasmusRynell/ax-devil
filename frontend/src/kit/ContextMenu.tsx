import { useState, type ReactNode } from "react";
import { Menu, type MenuCommand, type MenuEntry } from "./Menu";
import { Popup } from "./Popup";

const NOTHING_CHECKED: ReadonlySet<string> = new Set();

interface Props {
  /** The entries, or a function asked for them each time the menu opens, for entries that depend on what was clicked. */
  entries: readonly MenuEntry[] | (() => readonly MenuEntry[]);
  onSelect: (command: MenuCommand) => void;
  /** The ids of the `check` entries that are checked. */
  checked?: ReadonlySet<string>;
  /** Classes for the wrapping element, for content that fills a positioned parent. */
  className?: string;
  children: ReactNode;
}

/** Wraps content so a right click on it opens `entries` at the pointer. */
export function ContextMenu({
  entries,
  onSelect,
  checked = NOTHING_CHECKED,
  className,
  children,
}: Props) {
  const [at, setAt] = useState<{ x: number; y: number; asked: readonly MenuEntry[] | null } | null>(
    null,
  );
  return (
    <div
      className={className}
      onContextMenu={(event) => {
        event.preventDefault();
        setAt({
          x: event.clientX,
          y: event.clientY,
          asked: typeof entries === "function" ? entries() : null,
        });
      }}
    >
      {children}
      {at && (
        <Popup at={at} onClose={() => setAt(null)} className="z-20 min-w-max">
          <Menu
            entries={at.asked ?? (typeof entries === "function" ? [] : entries)}
            checked={checked}
            onSelect={(command) => {
              setAt(null);
              onSelect(command);
            }}
          />
        </Popup>
      )}
    </div>
  );
}
