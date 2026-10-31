import { useEffect, useRef, useState } from "react";
import { MENUS } from "../commands";
import { Menu, type MenuCommand } from "../kit/Menu";

interface Props {
  checked: ReadonlySet<string>;
  onSelect: (command: MenuCommand) => void;
}

const FIRST_ITEM = '[role="menu"] [role^="menuitem"]:not(:disabled)';

/**
 * The in-page menu bar. Alt focuses it, Left and Right move between menus, Down opens one, Escape closes it, and
 * hovering another menu while one is open switches to it.
 */
export function MenuBar({ checked, onSelect }: Props) {
  const [open, setOpen] = useState<number | null>(null);
  const bar = useRef<HTMLDivElement>(null);
  const focusItem = useRef(false);
  const tops = () => [...(bar.current?.querySelectorAll<HTMLElement>("[data-top]") ?? [])];

  useEffect(() => {
    if (open !== null && focusItem.current)
      bar.current?.querySelector<HTMLElement>(FIRST_ITEM)?.focus();
    focusItem.current = false;
  }, [open]);

  useEffect(() => {
    let altAlone = false;
    const down = (event: KeyboardEvent) => {
      altAlone = event.key === "Alt" && !event.repeat;
    };
    const up = (event: KeyboardEvent) => {
      if (event.key === "Alt" && altAlone) tops()[0]?.focus();
      altAlone = false;
    };
    const outside = (event: PointerEvent) => {
      if (!bar.current?.contains(event.target as Node)) setOpen(null);
    };
    document.addEventListener("keydown", down);
    document.addEventListener("keyup", up);
    document.addEventListener("pointerdown", outside);
    return () => {
      document.removeEventListener("keydown", down);
      document.removeEventListener("keyup", up);
      document.removeEventListener("pointerdown", outside);
    };
  }, []);

  const onKeyDown = (event: React.KeyboardEvent) => {
    const buttons = tops();
    const focused = (event.target as HTMLElement).closest<HTMLElement>("[data-top]");
    const current = open ?? (focused ? buttons.indexOf(focused) : -1);
    if (event.key === "Escape" && open !== null) {
      setOpen(null);
      buttons[open]?.focus();
    } else if ((event.key === "ArrowRight" || event.key === "ArrowLeft") && current >= 0) {
      const next =
        (current + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
      if (open !== null) setOpen(next);
      buttons[next]?.focus();
    } else if (event.key === "ArrowDown" && focused) {
      const index = buttons.indexOf(focused);
      if (open === index) bar.current?.querySelector<HTMLElement>(FIRST_ITEM)?.focus();
      else {
        focusItem.current = true;
        setOpen(index);
      }
    } else return;
    event.preventDefault();
  };

  return (
    <div
      ref={bar}
      role="menubar"
      aria-label="Menu"
      onKeyDown={onKeyDown}
      className="flex h-full items-center gap-xs px-s [-webkit-app-region:no-drag]"
    >
      {MENUS.map((menu, index) => (
        <div key={menu.label} className="relative">
          <button
            type="button"
            role="menuitem"
            data-top
            aria-haspopup="menu"
            aria-expanded={open === index}
            onClick={() => setOpen(open === index ? null : index)}
            onMouseEnter={() => open !== null && setOpen(index)}
            className="h-row rounded-control bg-transparent px-m whitespace-nowrap text-fg hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent aria-expanded:bg-hover"
          >
            {menu.label}
          </button>
          {open === index && (
            <div className="absolute top-full left-0 z-10 max-h-[calc(100vh-var(--spacing-bar))] min-w-max overflow-y-auto rounded-popup border border-line-strong bg-popup p-xs shadow-lg">
              <Menu
                entries={menu.entries}
                checked={checked}
                onSelect={(command) => {
                  setOpen(null);
                  tops()[index]?.focus();
                  onSelect(command);
                }}
              />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
