import { useState, type ReactNode } from "react";
import { Menu, type MenuCommand, type MenuEntry } from "./Menu";
import { Popup } from "./Popup";

const NOTHING_CHECKED: ReadonlySet<string> = new Set();

interface Props {
  entries: readonly MenuEntry[];
  onSelect: (command: MenuCommand) => void;
  /** Classes for the wrapping element, for content that fills a positioned parent. */
  className?: string;
  children: ReactNode;
}

/** Wraps content so a right click on it opens `entries` at the pointer. */
export function ContextMenu({ entries, onSelect, className, children }: Props) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  return (
    <div
      className={className}
      onContextMenu={(event) => {
        event.preventDefault();
        setAt({ x: event.clientX, y: event.clientY });
      }}
    >
      {children}
      {at && (
        <Popup at={at} onClose={() => setAt(null)} className="z-20 min-w-max">
          <Menu
            entries={entries}
            checked={NOTHING_CHECKED}
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
