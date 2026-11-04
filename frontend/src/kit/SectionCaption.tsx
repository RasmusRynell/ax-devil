import type { ReactNode } from "react";
import { Icon } from "./Icon";
import { TONE, type Kind, type Tone } from "./look";

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
  /** Shown right after the heading, unless zero; the heading's accessible name always carries it. */
  count?: number;
  /** Shown after the count as ` · note`, in the heading's row but not in capitals; it shortens first. */
  note?: string;
  /** Muted text at the right edge; it ends in an ellipsis when the heading and rule leave no room. */
  aside?: string;
  /** Colours the aside to mark trouble; muted when omitted. */
  asideTone?: Tone;
  /** The aside's tooltip; the aside itself when omitted. */
  asideTitle?: string;
  /** A control after the aside, such as a small Add button; it keeps its size and is not part of the fold click. */
  action?: ReactNode;
  /** Colour of the square before the heading; muted when omitted. */
  mark?: CaptionMark;
  /** Only the heading, without the square and the rule: the caption of a dialog's or a start page's own group, not of a list. */
  plain?: boolean;
  /** Makes the group foldable: a click on the caption, or its button at the far left, folds it; the action never does. */
  fold?: { open: boolean; onToggle: () => void };
}

/** A row-height group heading: small coloured square, capitals, count, a faint rule, then an optional aside and action. */
export function SectionCaption({
  heading,
  count,
  note,
  aside,
  asideTone = "neutral",
  asideTitle,
  action,
  mark = "muted",
  plain = false,
  fold,
}: Props) {
  return (
    <div className="flex h-row items-center gap-s px-m">
      {/* The button is the keyboard's way in; its click reaches the caption's, which does the folding. */}
      <div
        onClick={fold?.onToggle}
        className={`flex h-full min-w-0 flex-1 items-center gap-s ${fold ? "cursor-pointer" : ""}`}
      >
        {fold && (
          <button
            type="button"
            aria-label={heading}
            aria-expanded={fold.open}
            className="flex square-row shrink-0 items-center justify-center rounded-control bg-transparent text-muted hover:bg-hover focus-ring"
          >
            <Icon name={fold.open ? "chevron-down" : "chevron-right"} />
          </button>
        )}
        {!plain && <span aria-hidden className={`size-m shrink-0 rounded-mark ${MARK[mark]}`} />}
        <h2
          aria-label={
            count === undefined ? undefined : `${heading}, ${count}${note ? `, ${note}` : ""}`
          }
          className="m-0 flex min-w-0 text-small font-semibold tracking-caption whitespace-nowrap text-muted uppercase"
        >
          <span className="shrink-0">
            {heading}
            {count !== undefined && count > 0 && (
              <>
                {" "}
                <span className="ml-s tabular-nums">{count}</span>
              </>
            )}
          </span>
          {note && (
            <>
              {" "}
              <span className="ml-s min-w-0 truncate font-normal normal-case">· {note}</span>
            </>
          )}
        </h2>
        {!plain && <span aria-hidden className="h-hairline min-w-s flex-1 bg-fg/12" />}
        {aside && (
          <span
            title={asideTitle ?? aside}
            className={`min-w-0 shrink-100 truncate text-aside ${TONE[asideTone].text}`}
          >
            {aside}
          </span>
        )}
      </div>
      {action}
    </div>
  );
}
