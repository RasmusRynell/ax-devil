import { useLayoutEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import { ownKeys } from "./keys";
import { QUIET } from "./look";

const tabKeys = ownKeys(new Set(["ArrowLeft", "ArrowRight", "Home", "End"]));
const columnKeys = ownKeys(
  new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"]),
);

interface Props<T extends string> {
  /** The accessible name of the tab strip. */
  label: string;
  /** The tab names, in order; each is also its label. */
  tabs: readonly T[];
  value: T;
  onChange: (tab: T) => void;
  /** The `id` of the panel the selected tab shows. */
  panelId: string;
  /** Tabs whose controls are active, independently of selection. */
  active?: Partial<Record<T, boolean>>;
  /** How many a tab holds, shown after its name. */
  counts?: Partial<Record<T, number>>;
  /** Help available even when a tab has no entries. */
  help?: Partial<Record<T, string>>;
  /** Controls at the strip's far end, such as a close button. */
  trailing?: ReactNode;
  /** Keeps one row, scrolling the selected tab into view when space is limited or changes. */
  scrollable?: boolean;
  /**
   * `vertical` stands the tabs in a column of the `spacing-nav` width, for a strip beside its panel in a flex row
   * inside a `@container`; there the up and down arrow keys also move. In a container narrower than `container-pages`
   * it is the scrolling strip again, and the row around it must stack.
   */
  orientation?: "vertical";
}

/** A strip of tabs over one panel; the left and right arrow keys, Home and End move between them. */
export function Tabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
  panelId,
  counts = {},
  active = {},
  help = {},
  trailing,
  scrollable = false,
  orientation,
}: Props<T>) {
  const column = orientation === "vertical";
  const scrolls = scrollable || column;
  const keys = column ? columnKeys : tabKeys;
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const strip = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const row = strip.current;
    const tab = buttons.current[tabs.indexOf(value)];
    if (!scrolls || !row || !tab) return;
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
  }, [scrolls, tabs, value]);
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, at: number) => {
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    const last = tabs.length - 1;
    const next = event.key === "ArrowRight" || (column && event.key === "ArrowDown");
    const previous = event.key === "ArrowLeft" || (column && event.key === "ArrowUp");
    const to = next
      ? (at + 1) % tabs.length
      : previous
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
      aria-orientation={column ? "vertical" : undefined}
      className={`flex border-b border-line ${scrolls ? "min-w-0 shrink-0 overflow-x-auto overflow-y-hidden" : ""} ${column ? "@pages:w-nav @pages:flex-col @pages:self-start @pages:overflow-visible @pages:border-b-0 @pages:border-l" : ""}`}
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
          data-active={active[name] ?? false}
          aria-selected={name === value}
          aria-controls={panelId}
          tabIndex={name === value ? 0 : -1}
          onClick={() => onChange(name)}
          onKeyDown={(event) => {
            keys(event);
            onKeyDown(event, at);
          }}
          onKeyUp={keys}
          className={`${QUIET} ${scrolls ? "shrink-0 whitespace-nowrap" : "-mb-hairline"} ${column ? "focus-ring-inset aria-selected:bg-accent/25 aria-selected:font-medium aria-selected:enabled:hover:bg-accent/25 @pages:-ml-hairline @pages:border-b-0 @pages:border-l-edge" : "[--stroke-focus:var(--spacing-hairline)]"} border-b-edge border-transparent aria-selected:border-accent data-[active=true]:bg-accent/15 data-[active=true]:text-accent`}
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
