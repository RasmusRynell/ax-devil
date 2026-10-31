import { useState } from "react";
import { Icon } from "./Icon";

export type MenuEntry =
  | { kind: "separator" }
  | { kind: "action" | "check"; id: string; label: string; shortcut?: string; disabled?: boolean }
  | { kind: "submenu"; label: string; items: readonly MenuEntry[] };

export type MenuCommand = Extract<MenuEntry, { id: string }>;

const ITEMS = '[role^="menuitem"]:not(:disabled)';

interface Props {
  entries: readonly MenuEntry[];
  checked: ReadonlySet<string>;
  onSelect: (command: MenuCommand) => void;
}

/**
 * A menu panel. Up and Down move through the items, Right and Left open and close a submenu (which expands in place),
 * Enter runs the focused item. Left, Right and Escape that it does not use bubble to the menu bar.
 */
export function Menu({ entries, checked, onSelect }: Props) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const toggle = (label: string, open: boolean) =>
    setExpanded((now) => {
      const next = new Set(now);
      if (open) next.add(label);
      else next.delete(label);
      return next;
    });
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const items = [...event.currentTarget.querySelectorAll<HTMLElement>(ITEMS)];
    const at = items.indexOf(document.activeElement as HTMLElement);
    const go = (index: number) => items[(index + items.length) % items.length]?.focus();
    const active = document.activeElement as HTMLElement | null;
    const header = active
      ?.closest("[data-submenu]")
      ?.querySelector<HTMLElement>(":scope > [aria-haspopup]");
    if (event.key === "ArrowDown") go(at + 1);
    else if (event.key === "ArrowUp") go(at < 0 ? -1 : at - 1);
    else if (event.key === "Home") go(0);
    else if (event.key === "End") go(-1);
    else if (event.key === "ArrowRight" && active?.hasAttribute("aria-haspopup")) {
      toggle(active.dataset.label ?? "", true);
    } else if (event.key === "ArrowLeft" && header?.ariaExpanded === "true") {
      toggle(header.dataset.label ?? "", false);
      header.focus();
    } else return;
    event.preventDefault();
    event.stopPropagation();
  };
  return (
    <div role="menu" onKeyDown={onKeyDown} className="flex flex-col">
      <Entries
        entries={entries}
        checked={checked}
        expanded={expanded}
        toggle={toggle}
        onSelect={onSelect}
      />
    </div>
  );
}

interface EntriesProps extends Props {
  expanded: ReadonlySet<string>;
  toggle: (label: string, open: boolean) => void;
}

const ROW =
  "flex h-row w-full items-center gap-m rounded-control bg-transparent px-m text-left text-fg focus:bg-hover focus:outline-none disabled:text-disabled";

function Entries({ entries, checked, expanded, toggle, onSelect }: EntriesProps) {
  const hasChecks = entries.some((entry) => entry.kind === "check");
  return entries.map((entry, index) => {
    if (entry.kind === "separator")
      return <div key={index} role="separator" className="my-xs border-t border-line" />;
    if (entry.kind === "submenu") {
      const open = expanded.has(entry.label);
      return (
        <div key={entry.label} data-submenu className="flex flex-col">
          <button
            type="button"
            role="menuitem"
            aria-haspopup="menu"
            aria-expanded={open}
            tabIndex={-1}
            data-label={entry.label}
            onClick={() => toggle(entry.label, !open)}
            onMouseEnter={(event) => event.currentTarget.focus()}
            className={ROW}
          >
            {hasChecks && <span className="size-icon shrink-0" />}
            <span className="flex-1 whitespace-nowrap">{entry.label}</span>
            <Icon name={open ? "chevron-down" : "chevron-right"} />
          </button>
          {open && (
            <div role="menu" className="flex flex-col pl-xl">
              <Entries
                entries={entry.items}
                checked={checked}
                expanded={expanded}
                toggle={toggle}
                onSelect={onSelect}
              />
            </div>
          )}
        </div>
      );
    }
    const isChecked = checked.has(entry.id);
    return (
      <button
        key={entry.id}
        type="button"
        role={entry.kind === "check" ? "menuitemcheckbox" : "menuitem"}
        aria-checked={entry.kind === "check" ? isChecked : undefined}
        disabled={entry.disabled}
        tabIndex={-1}
        onClick={() => onSelect(entry)}
        onMouseEnter={(event) => event.currentTarget.focus()}
        className={ROW}
      >
        {hasChecks && (
          <span className="size-icon shrink-0">{isChecked && <Icon name="check" />}</span>
        )}
        <span className="flex-1 whitespace-nowrap">{entry.label}</span>
        {entry.shortcut && <span className="ml-xl text-muted">{entry.shortcut}</span>}
      </button>
    );
  });
}
