import { useEffect, useRef } from "react";

export interface CheckItem {
  id: string;
  label: string;
  /** Muted text after the label, such as a size. */
  detail?: string;
}

interface Props {
  /** The accessible name of the list. */
  label: string;
  items: readonly CheckItem[];
  checked: ReadonlySet<string>;
  onToggle: (id: string) => void;
  /** Called by the box above the list: checks everything, or unchecks everything when all are checked. */
  onToggleAll: () => void;
}

const ROW = "flex min-h-row items-center gap-m px-m hover:bg-hover";

/** A list of items with a box each and one above them for all; it scrolls when it grows past a few rows. */
export function CheckList({ label, items, checked, onToggle, onToggleAll }: Props) {
  const all = useRef<HTMLInputElement>(null);
  const count = items.filter((item) => checked.has(item.id)).length;
  useEffect(() => {
    if (all.current) all.current.indeterminate = count > 0 && count < items.length;
  }, [count, items.length]);
  return (
    <div role="group" aria-label={label} className="flex min-w-0 flex-col">
      <label className={`${ROW} border-b border-line`}>
        <input
          ref={all}
          type="checkbox"
          checked={items.length > 0 && count === items.length}
          onChange={onToggleAll}
          className="size-icon accent-accent"
        />
        All
      </label>
      <ul className="m-0 flex max-h-[calc(var(--spacing-row)*8)] list-none flex-col overflow-y-auto p-0">
        {items.map((item) => (
          <li key={item.id}>
            <label className={ROW}>
              <input
                type="checkbox"
                checked={checked.has(item.id)}
                onChange={() => onToggle(item.id)}
                className="size-icon shrink-0 accent-accent"
              />
              <span className="min-w-0 flex-1">{item.label}</span>
              {item.detail && <span className="text-muted">{item.detail}</span>}
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}
