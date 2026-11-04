import { Icon } from "./Icon";
import type { Kind, Tone } from "./look";

/** What colours the small square before the title: the muted default, the accent, a status tone or a kind. */
export type CaptionMark = "muted" | "accent" | Tone | Kind;

const MARK: Record<CaptionMark, string> = {
  muted: "bg-muted",
  accent: "bg-accent",
  neutral: "bg-muted",
  success: "bg-success",
  caution: "bg-caution",
  danger: "bg-danger",
  live: "bg-kind-live",
  video: "bg-kind-video",
  playlist: "bg-kind-playlist",
  lane: "bg-kind-lane",
};

interface Props {
  /** The group name, drawn in capitals; it never shortens. */
  heading: string;
  /** Shown right after the heading. */
  count?: number;
  /** Shown after the count as ` · note`, in the heading's row but not in capitals. */
  note?: string;
  /** Muted text at the right edge; it ends in an ellipsis when the heading and rule leave no room. */
  aside?: string;
  /** Colour of the square before the heading; muted when omitted. */
  mark?: CaptionMark;
  /** Only the heading, without the square and the rule: the caption of a dialog's or a start page's own group, not of a list. */
  plain?: boolean;
  /** Makes the group foldable through a button at the far left; the caption itself is not clickable. */
  fold?: { open: boolean; onToggle: () => void };
}

/** A row-height group heading: small coloured square, capitals, count, a faint rule, then an optional aside. */
export function SectionCaption({
  heading,
  count,
  note,
  aside,
  mark = "muted",
  plain = false,
  fold,
}: Props) {
  return (
    <div className="flex h-row items-center gap-s px-m">
      {fold && (
        <button
          type="button"
          aria-label={heading}
          aria-expanded={fold.open}
          onClick={fold.onToggle}
          className="flex square-row shrink-0 items-center justify-center rounded-control bg-transparent text-muted hover:bg-hover focus-ring"
        >
          <Icon name={fold.open ? "chevron-down" : "chevron-right"} />
        </button>
      )}
      {!plain && <span aria-hidden className={`size-m shrink-0 rounded-mark ${MARK[mark]}`} />}
      <h2 className="m-0 shrink-0 text-small font-semibold tracking-caption whitespace-nowrap text-muted uppercase">
        {heading}
        {count !== undefined && (
          <>
            {" "}
            <span className="ml-s tabular-nums">{count}</span>
          </>
        )}
        {note && (
          <>
            {" "}
            <span className="font-normal normal-case">· {note}</span>
          </>
        )}
      </h2>
      {!plain && <span aria-hidden className="h-hairline min-w-s flex-1 bg-fg/12" />}
      {aside && (
        <span title={aside} className="min-w-0 truncate text-aside text-muted">
          {aside}
        </span>
      )}
    </div>
  );
}
