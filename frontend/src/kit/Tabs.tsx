import { useRef, type KeyboardEvent } from "react";
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
}

/** A strip of tabs over one panel; the left and right arrow keys, Home and End move between them. */
export function Tabs<T extends string>({ label, tabs, value, onChange, panelId }: Props<T>) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
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
    <div role="tablist" aria-label={label} className="flex border-b border-line">
      {tabs.map((name, at) => (
        <button
          key={name}
          ref={(node) => {
            buttons.current[at] = node;
          }}
          type="button"
          role="tab"
          aria-selected={name === value}
          aria-controls={panelId}
          tabIndex={name === value ? 0 : -1}
          onClick={() => onChange(name)}
          onKeyDown={(event) => {
            tabKeys(event);
            onKeyDown(event, at);
          }}
          onKeyUp={tabKeys}
          className={`${QUIET} -mb-hairline border-b-edge border-transparent aria-selected:border-accent`}
        >
          {name}
        </button>
      ))}
    </div>
  );
}
