import { useLayoutEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import { ownKeys } from "./keys";
import { QUIET } from "./look";

const tabKeys = ownKeys(new Set(["ArrowLeft", "ArrowRight", "Home", "End"]));

interface Props<T extends string> {
  /** The accessible name of the tab strip. */
  label: string;
  /** The tab names, in order; each is also its label. */
  tabs: readonly T[];
  value: T;
  onChange: (tab: T) => void;
  /** The `id` of the panel the selected tab shows. */
  panelId: string;
  /** How many a tab holds, shown after its name. */
  counts?: Partial<Record<T, number>>;
  /** Help available even when a tab has no entries. */
  help?: Partial<Record<T, string>>;
  /** Controls at the strip's far end, such as a close button. */
  trailing?: ReactNode;
  /** Keeps one row, scrolling the selected tab into view when space is limited or changes. */
  scrollable?: boolean;
}

/** A strip of tabs over one panel; the left and right arrow keys, Home and End move between them. */
export function Tabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
  panelId,
  counts = {},
  help = {},
  trailing,
  scrollable = false,
}: Props<T>) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const strip = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const row = strip.current;
    const tab = buttons.current[tabs.indexOf(value)];
    if (!scrollable || !row || !tab) return;
    const reveal = () => {
      const box = row.getBoundingClientRect();
      const selected = tab.getBoundingClientRect();
      const left = box.left + row.clientLeft;
      const right = left + row.clientWidth;
      if (selected.left < left) row.scrollLeft -= left - selected.left;
      else if (selected.right > right) row.scrollLeft += selected.right - right;
    };
    reveal();
    const observer = new ResizeObserver(reveal);
    observer.observe(row);
    observer.observe(tab);
    return () => observer.disconnect();
  }, [scrollable, tabs, value]);
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, at: number) => {
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    const last = tabs.length - 1;
    const to =
      event.key === "ArrowRight"
        ? (at + 1) % tabs.length
        : event.key === "ArrowLeft"
          ? (at + last) % tabs.length
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? last
              : undefined;
    const tab = to === undefined ? undefined : tabs[to];
    if (tab === undefined) return;
    event.preventDefault();
    onChange(tab);
    buttons.current[to ?? 0]?.focus();
  };
  return (
    <div
      ref={strip}
      role="tablist"
      aria-label={label}
      className={`flex border-b border-line ${scrollable ? "min-w-0 shrink-0 overflow-x-auto overflow-y-hidden" : ""}`}
    >
      {tabs.map((name, at) => (
        <button
          key={name}
          ref={(node) => {
            buttons.current[at] = node;
          }}
          type="button"
          role="tab"
          title={help[name]}
          aria-description={help[name]}
          aria-selected={name === value}
          aria-controls={panelId}
          tabIndex={name === value ? 0 : -1}
          onClick={() => onChange(name)}
          onKeyDown={(event) => {
            tabKeys(event);
            onKeyDown(event, at);
          }}
          onKeyUp={tabKeys}
          className={`${QUIET} ${scrollable ? "shrink-0 whitespace-nowrap" : "-mb-hairline"} [--stroke-focus:var(--spacing-hairline)] border-b-edge border-transparent aria-selected:border-accent`}
        >
          {name}
          {counts[name] !== undefined && (
            <>
              {" "}
              <span className="ml-s text-small text-muted tabular-nums">{counts[name]}</span>
            </>
          )}
        </button>
      ))}
      {trailing && (
        <div role="presentation" className="ml-auto flex min-w-0 items-center gap-s">
          {trailing}
        </div>
      )}
    </div>
  );
}
