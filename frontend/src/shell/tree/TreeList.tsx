import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type RefObject,
  type SyntheticEvent,
} from "react";
import { Button } from "../../kit/Button";
import { Chip } from "../../kit/Chips";
import { Icon } from "../../kit/Icon";
import { IconButton } from "../../kit/IconButton";
import type { IconName } from "../../kit/icons";
import { KIND } from "../../kit/look";
import { NoticeList, type Notice } from "../../kit/NoticeList";
import { Popover } from "../../kit/Popover";
import type { MarkColor } from "../../live/streamState";
import { HoverPicture } from "../HoverPicture";
import { useHoverPicture } from "../useHoverPicture";
import { useSpanWindow } from "../useSpanWindow";
import { treeKey, type TreeKeyRow } from "./treeKeys";

/** The colour of a mark by its state. */
export const MARK_COLOR: Record<MarkColor, string> = {
  live: KIND.live,
  caution: "text-caution",
  muted: "text-muted",
};

/** Which view's looks a tree wears. */
export type Variant = "live" | "workspace";

/** A button at the right of a row's first line. */
export type RowButton = {
  id: string;
  icon: IconName;
  label: string;
  disabled: boolean;
  /** A borderless icon, not a bordered button. */
  plain?: boolean;
  /** Drawn only while the pointer is over the row or the keyboard is on it. */
  faint?: boolean;
};

/** What a row shows, decided by its view; the tree draws it and decides nothing. */
export type RowLook = {
  label: string;
  mark: { icon: IconName; className: string };
  /** A short chip before the label. */
  tag?: string;
  /** Muted text after the label, right-aligned; it ends in an ellipsis before the label does. */
  aside?: string;
  /** Muted text under the label. */
  detail: string;
  detailWarning?: boolean;
  /** More muted lines under the detail. */
  lines?: readonly string[];
  /** What a screen reader adds after the label. */
  note?: string;
  /** The tooltip, left out for a row with a picture, which would show beside its card. */
  title: string;
  /** The URL of the picture a hover shows. */
  picture?: string | null;
  dim: boolean;
  /** Whether a viewer shows it, which gives the accent edge. */
  open: boolean;
  busy?: boolean;
  warnings?: readonly Notice[];
  buttons: readonly RowButton[];
};

/** A row placed in its tree: what the keys need, where it stands, how it looks, and the view's own row. */
export type Placed<S> = TreeKeyRow & {
  level: number;
  /** Its place among the rows that share its parent, from 1, and how many they are; the tree draws only some. */
  position: number;
  siblings: number;
  look: RowLook;
  row: S;
};

/** How one view draws its rows. */
type VariantLook = {
  /** The box the window measures, indented by level, with the fold button in it; none: the row itself is measured. */
  wrapper: ((level: number) => string) | null;
  indent: (level: number) => CSSProperties | undefined;
  item: string;
  /** The first line of a row, the gap in the lines under it, and their columns of icon width before the text. */
  line: string;
  gap: string;
  columns: number;
  /** Whether the fold arrow is a button of its own outside the row, or a part of its first line. */
  button: boolean;
};

const VARIANTS: Record<Variant, VariantLook> = {
  live: {
    wrapper: (level) => `relative pl-l ${level > 1 ? "ml-l" : ""}`,
    indent: () => undefined,
    item: "flex min-h-row flex-col gap-xs border-l-edge border-transparent py-xs pr-m pl-m hover:bg-hover",
    line: "flex items-center gap-m",
    gap: "gap-m",
    columns: 1,
    button: true,
  },
  workspace: {
    wrapper: null,
    indent: (level) => ({
      paddingLeft: `calc(var(--spacing-m) + var(--spacing-xl) * ${level - 1})`,
    }),
    item: "group/row flex min-h-row shrink-0 cursor-pointer flex-col justify-center border-l-edge border-transparent pr-s hover:bg-hover",
    line: "flex h-row items-center gap-s",
    gap: "gap-s",
    columns: 2,
    button: false,
  },
};

const halt = (event: SyntheticEvent) => event.stopPropagation();

interface Props<S> {
  variant: Variant;
  label: string;
  /** Makes the ids of the rows unique among the trees of the page. */
  prefix: string;
  rows: readonly Placed<S>[];
  /** The scrolling element that holds the tree, which draws only the rows in view. */
  scroller: RefObject<HTMLElement | null>;
  /** The key of the row the keyboard is on, which may be in another tree. */
  current: string | null;
  /** Whether Ctrl, Alt and Meta keys are the tree's (see `treeKey`). */
  chords: boolean;
  onCurrent: (key: string) => void;
  /** The keyboard moved to a place in the rows, which may be outside them. */
  onMove: (to: number | "first" | "last") => void;
  onFold: (row: Placed<S>, collapse: boolean) => void;
  /** Enter, F2, Delete, Space or a double click, to the `side` with Ctrl or by the `pointer`. */
  onCommand: (
    row: Placed<S>,
    command: "open" | "edit" | "remove" | "info",
    how: { side: boolean; pointer: boolean },
  ) => void;
  onButton: (row: Placed<S>, id: string) => void;
  /** The menu of a row at a point, and the tree to give the focus back to when it closes. */
  onMenu: (row: Placed<S>, x: number, y: number, tree: HTMLElement | null) => void;
  /** The tree went away, holding the focus or not; the focus is its owner's to give to something that stays. */
  onGone?: (focused: boolean) => void;
}

/**
 * One section's rows as a tree that draws only the rows in view, however many a scan lists, with the hover card of
 * the row the pointer rests on. The keys are `treeKey`'s; a key that reaches it from a control inside a row is the
 * control's. Focusing the tree puts the keyboard on its first row unless it is already on one of its rows.
 */
export function TreeList<S>(props: Props<S>) {
  const { rows, current, onCurrent, onCommand, onFold, onMenu } = props;
  const { ref, start, end, before, after, reveal } = useSpanWindow(
    rows.map(({ key }) => key),
    props.scroller,
  );
  const picture = useHoverPicture(
    useMemo(() => new Set(rows.flatMap(({ look }) => look.picture ?? [])), [rows]),
  );
  const look = VARIANTS[props.variant];
  const at = rows.findIndex(({ key }) => key === current);
  const idOf = (index: number) => `${props.prefix}-${index}`;

  // Every move brings its row into view, also a move to the row the keyboard is already on.
  const [moves, setMoves] = useState(0);
  useEffect(() => {
    if (at >= 0) reveal(at);
  }, [current, moves]);
  const gone = useRef(props.onGone);
  gone.current = props.onGone;
  useLayoutEffect(() => {
    const tree = ref.current;
    return () => {
      gone.current?.(!!tree?.contains(document.activeElement));
    };
  }, [ref]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    const from = Math.max(at, 0);
    const effect = treeKey(event, rows, at, { chords: props.chords });
    const row = rows[from];
    if (!effect || !row) return;
    event.preventDefault();
    if ("move" in effect) {
      setMoves((now) => now + 1);
      props.onMove(effect.move);
    } else if ("fold" in effect) onFold(row, effect.collapse);
    else if (effect.command === "menu") {
      const box = document.getElementById(idOf(from))?.getBoundingClientRect();
      onMenu(row, box?.left ?? 0, box?.bottom ?? 0, ref.current);
    } else {
      const side = effect.command === "open" && effect.side;
      onCommand(row, effect.command, { side, pointer: false });
    }
  };

  /** A line of muted text under a row's first line, starting where its label does. */
  const lineOf = (text: string, spoken: boolean, warning = false) => (
    <div key={text} className={`flex ${look.gap}`}>
      {Array.from({ length: look.columns }, (_, column) => (
        <span key={column} aria-hidden className="size-icon shrink-0" />
      ))}
      {spoken && <span className="sr-only">, </span>}
      <span className={`min-w-0 truncate text-small ${warning ? "text-caution" : "text-muted"}`}>
        {text}
      </span>
    </div>
  );

  return (
    <>
      <div
        ref={ref}
        role="tree"
        aria-label={props.label}
        aria-activedescendant={at >= start && at < end ? idOf(at) : undefined}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onFocus={(event) => {
          // The keyboard starts on the first row, so the first arrow key moves on from it.
          const first = rows[0];
          if (event.target === event.currentTarget && at < 0 && first) onCurrent(first.key);
        }}
        // The rows left out stand in as padding, which only their measured heights can give.
        style={{ paddingTop: before, paddingBottom: after }}
        className="group/tree flex flex-col focus:outline-none"
      >
        {rows.slice(start, end).map((placed, offset) => {
          const { key, level, expanded, position, siblings, can } = placed;
          const shown = placed.look;
          const index = start + offset;
          const isCurrent = index === at;
          const fold = () => onFold(placed, expanded === true);
          const chevron = expanded !== null && (
            <Icon
              name={expanded ? "chevron-down" : "chevron-right"}
              className={look.button ? "scale-75" : undefined}
            />
          );
          const item = (
            <div
              key={key}
              id={idOf(index)}
              role="treeitem"
              data-span={look.wrapper ? undefined : key}
              data-cursor-id={`row:${key}`}
              aria-level={level}
              aria-posinset={position}
              aria-setsize={siblings}
              aria-expanded={expanded ?? undefined}
              aria-selected={isCurrent}
              aria-current={shown.open || undefined}
              aria-busy={shown.busy || undefined}
              title={(!shown.picture && shown.title) || undefined}
              onClick={() => {
                // The focus first: a tree that takes it starts on its first row, which the click then overrides.
                ref.current?.focus({ preventScroll: true });
                onCurrent(key);
                if (can.info) onCommand(placed, "info", { side: false, pointer: true });
              }}
              onDoubleClick={(event) =>
                onCommand(placed, "open", { side: event.ctrlKey, pointer: true })
              }
              onContextMenu={(event) => {
                if (!can.menu) return;
                event.preventDefault();
                onCurrent(key);
                onMenu(placed, event.clientX, event.clientY, ref.current);
              }}
              onPointerEnter={(event) =>
                picture.enter(shown.picture ?? null, event.clientX, event.clientY)
              }
              onPointerLeave={picture.leave}
              style={look.indent(level)}
              className={`${look.item} ${shown.dim ? "text-muted" : "text-fg"} aria-[current=true]:border-accent aria-[current=true]:bg-hover ${
                isCurrent
                  ? "bg-hover group-focus-visible/tree:outline-(length:--stroke-focus) group-focus-visible/tree:-outline-offset-(--stroke-focus) group-focus-visible/tree:outline-accent"
                  : ""
              }`}
            >
              <div className={look.line}>
                {!look.button &&
                  (chevron ? (
                    <span
                      aria-hidden
                      onClick={(event) => {
                        event.stopPropagation();
                        fold();
                      }}
                      onDoubleClick={halt}
                    >
                      {chevron}
                    </span>
                  ) : (
                    <span aria-hidden className="size-icon shrink-0" />
                  ))}
                <Icon name={shown.mark.icon} className={shown.mark.className} />
                {shown.tag && <Chip>{shown.tag}</Chip>}
                <span className="min-w-title-least shrink truncate">{shown.label}</span>
                <span aria-hidden className="min-w-0 flex-1" />
                {shown.note && <span className="sr-only">{`, ${shown.note}`}</span>}
                {shown.aside && (
                  <span className="min-w-0 shrink-1000 truncate text-small text-muted">
                    {shown.aside}
                  </span>
                )}
                {shown.warnings?.length ? (
                  <span onClick={halt} onDoubleClick={halt}>
                    <Popover
                      label={`${shown.label}: warnings`}
                      trigger={<Icon name="triangle-alert" />}
                      className="size-row justify-center"
                    >
                      <NoticeList notices={shown.warnings} />
                    </Popover>
                  </span>
                ) : null}
                {shown.buttons.map(({ id, icon, label: name, disabled, plain, faint }) => {
                  const press = (event: SyntheticEvent) => {
                    event.stopPropagation();
                    props.onButton(placed, id);
                  };
                  return plain ? (
                    <IconButton
                      key={id}
                      icon={icon}
                      label={name}
                      tabIndex={-1}
                      disabled={disabled}
                      onClick={press}
                      onDoubleClick={halt}
                      className={`rounded-control ${
                        faint && !isCurrent
                          ? "opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100"
                          : ""
                      }`}
                    />
                  ) : (
                    <Button
                      key={id}
                      size="small"
                      icon={icon}
                      label={name}
                      tabIndex={-1}
                      data-cursor-id={`action:${key}`}
                      disabled={disabled}
                      onClick={press}
                      onDoubleClick={halt}
                    />
                  );
                })}
              </div>
              {shown.detail && lineOf(shown.detail, true, shown.detailWarning)}
              {shown.lines?.map((line) => lineOf(line, false))}
            </div>
          );
          return look.wrapper ? (
            <div key={key} data-span={key} className={look.wrapper(level)}>
              {chevron && (
                <button
                  type="button"
                  tabIndex={-1}
                  aria-label={`${expanded ? "Fold" : "Unfold"} ${shown.label}`}
                  onClick={fold}
                  className="absolute top-0 left-0 flex h-row w-l items-center justify-center text-muted"
                >
                  {chevron}
                </button>
              )}
              {item}
            </div>
          ) : (
            item
          );
        })}
      </div>
      {picture.shown && <HoverPicture {...picture.shown} />}
    </>
  );
}
