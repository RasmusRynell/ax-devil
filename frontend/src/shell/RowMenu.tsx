import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { Menu, type MenuCommand, type MenuEntry } from "../kit/Menu";

const NOTHING_CHECKED: ReadonlySet<string> = new Set();

/** Keeps `[start, start + size]` inside `[0, limit]`, preferring the start. */
function within(start: number, size: number, limit: number): number {
  return Math.max(0, Math.min(start, limit - size));
}

/**
 * A menu opened at a point, such as on a tree row. Escape or a click elsewhere closes it, and so does choosing an
 * item; the first item has the focus while it is open.
 */
export function RowMenu({
  entries,
  x,
  y,
  onSelect,
  onClose,
}: {
  entries: readonly MenuEntry[];
  x: number;
  y: number;
  onSelect: (command: MenuCommand) => void;
  onClose: () => void;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const closer = useRef(onClose);
  const [place, setPlace] = useState<CSSProperties>({ left: x, top: y });

  useLayoutEffect(() => {
    if (!panel.current) return;
    const { offsetWidth, offsetHeight } = panel.current;
    setPlace({
      left: within(x, offsetWidth, window.innerWidth),
      top: within(y, offsetHeight, window.innerHeight),
    });
  }, [x, y]);

  useEffect(() => {
    closer.current = onClose;
  });

  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node)) closer.current();
    };
    document.addEventListener("pointerdown", outside);
    panel.current?.querySelector<HTMLElement>('[role^="menuitem"]:not(:disabled)')?.focus();
    return () => document.removeEventListener("pointerdown", outside);
  }, []);

  return (
    <div
      ref={panel}
      style={place}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }}
      onKeyUp={(event) => event.stopPropagation()}
      className="fixed z-10 w-max rounded-popup border border-line-strong bg-popup p-xs text-fg shadow-popup"
    >
      <Menu
        entries={entries}
        checked={NOTHING_CHECKED}
        onSelect={(command) => {
          onClose();
          onSelect(command);
        }}
      />
    </div>
  );
}
