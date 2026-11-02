import { Icon } from "./Icon";

interface Props {
  /** The group name, drawn in capitals; it never shortens. */
  heading: string;
  /** Shown after the heading as ` · count`. */
  count?: number;
  /** Small muted text at the right edge; it ends in an ellipsis when the heading and rule leave no room. */
  aside?: string;
  /** Makes the group foldable through a button before the heading; the caption itself is not clickable. */
  fold?: { open: boolean; onToggle: () => void };
}

/** A row-height group heading with a small square mark and a faint rule, the same height with or without an aside. */
export function SectionCaption({ heading, count, aside, fold }: Props) {
  return (
    <div className="flex h-row items-center gap-s px-m">
      {fold && (
        <button
          type="button"
          aria-label={heading}
          aria-expanded={fold.open}
          onClick={fold.onToggle}
          className="flex shrink-0 items-center rounded-control bg-transparent text-muted hover:bg-hover focus-ring"
        >
          <Icon name={fold.open ? "chevron-down" : "chevron-right"} />
        </button>
      )}
      <span aria-hidden className="size-m shrink-0 bg-accent" />
      <h2 className="m-0 shrink-0 text-small font-semibold tracking-wide text-muted uppercase">
        {count === undefined ? heading : `${heading} · ${count}`}
      </h2>
      <span aria-hidden className="h-hairline min-w-s flex-1 bg-line" />
      {aside && (
        <span title={aside} className="min-w-0 truncate text-small text-muted">
          {aside}
        </span>
      )}
    </div>
  );
}
