import { useEffect, useLayoutEffect, type KeyboardEvent, type RefObject } from "react";
import { Icon } from "../kit/Icon";
import type { IconName } from "../kit/icons";
import { IconButton } from "../kit/IconButton";
import { KIND, type Kind } from "../kit/look";
import { NoticeList, noticeOf } from "../kit/NoticeList";
import { Popover } from "../kit/Popover";
import type { SectionId, TreeRow, VisibleRow } from "./treeModel";
import { pictureOf } from "./useRowThumbnail";
import { useSpanWindow } from "./useSpanWindow";

const KIND_ICONS: Record<Kind, IconName> = {
  live: "video",
  video: "square-play",
  playlist: "list-video",
  lane: "layers",
};

/** The icon in front of a row and its colour: its kind's, a warning when unavailable, muted when hidden or pending. */
function iconOf({ info, playlist, dimmed }: TreeRow): { name: IconName; className: string } {
  const { status } = info.status;
  if (status === "Unavailable") return { name: "triangle-alert", className: "text-caution" };
  const kind: Kind = info.kind === "lane" ? "lane" : playlist ? "playlist" : "video";
  return { name: KIND_ICONS[kind], className: dimmed || status !== "Ready" ? "" : KIND[kind] };
}

/** Why a row cannot be opened as it is or what resolving left out, for assistive technology and the tooltip. */
function noteOf({ info, dimmed }: TreeRow): string {
  const { status } = info.status;
  // A seat that may not read the host's failures is told the failure, not why.
  if (status === "Unavailable")
    return info.status.reason ? `unavailable: ${info.status.reason}` : "unavailable";
  if (status === "Pending") return "opening";
  const warned = info.warnings.map(({ problem }) => problem).join("; ");
  return [dimmed ? "hidden" : "", warned].filter(Boolean).join(", ");
}

interface Props {
  id: SectionId;
  label: string;
  /** Makes the ids of the rows unique among the trees of the page. */
  prefix: string;
  rows: readonly VisibleRow[];
  /** How many rows the sections before this one list, which the keyboard walks on from. */
  offset: number;
  /** The key of the row the keyboard is on, which may be in another section's tree. */
  current: string | null;
  collapsed: ReadonlySet<string>;
  /** The scrolling element that holds the tree, which draws only the rows in view. */
  scroller: RefObject<HTMLElement | null>;
  /** Whether the seat may hide and unhide rows. */
  hides: boolean;
  /** Open the row, or with `side` (Ctrl) to the side of the front pane. */
  onOpen: (row: TreeRow, side?: boolean) => void;
  /** A double click shows the row and the rows above it, before it opens. */
  onReveal: (row: TreeRow) => void;
  /** Puts the keyboard on a row. */
  onCurrent: (key: string) => void;
  /** Moves the keyboard to the row at a place in the sequence of all sections' rows. */
  onGo: (index: number) => void;
  /** The tree took the focus with it when it went away, which the owner gives to a row or caption that stays. */
  onLose: (id: SectionId) => void;
  onFold: (key: string, collapse: boolean) => void;
  onHide: (row: TreeRow) => void;
  /** F2 and Delete on a row: edit or remove its item. */
  onEdit: (row: TreeRow) => void;
  onRemove: (row: TreeRow) => void;
  /** The menu of a row at a point, and the tree to give the focus back to when it closes. */
  onMenu: (row: TreeRow, x: number, y: number, tree: HTMLElement | null) => void;
  /** The pointer entered a row at a point (with the entry whose picture it shows), or left it. */
  thumbnail: {
    enter: (entry: string | null, x: number, y: number) => void;
    leave: () => void;
  };
}

/**
 * One section's rows as a tree that draws only the rows in view. A click selects a row; a double click or Enter
 * opens it in its viewer; the arrow keys move (on into the next section), expand and collapse; the menu key or Shift+F10
 * opens the row's menu.
 * Focusing the tree puts the keyboard on its first row unless it is already on one of its rows.
 */
export function WorkspaceList({
  id,
  label,
  prefix,
  rows,
  offset,
  current,
  collapsed,
  scroller,
  hides,
  onOpen,
  onReveal,
  onCurrent,
  onGo,
  onLose,
  onFold,
  onHide,
  onEdit,
  onRemove,
  onMenu,
  thumbnail,
}: Props) {
  const { ref, start, end, before, after, reveal } = useSpanWindow(
    rows.map(({ row }) => row.info.key),
    scroller,
  );

  const at = rows.findIndex(({ row }) => row.info.key === current);
  const from = Math.max(at, 0);
  const idOf = (index: number) => `${prefix}-${index}`;

  // The row the keyboard moved to scrolls into view, drawn yet or not.
  useEffect(() => {
    if (at >= 0) reveal(at);
  }, [current]);
  // A tree that goes away holding the focus leaves it to the owner to place.
  useLayoutEffect(() => {
    const tree = ref.current;
    return () => {
      if (tree?.contains(document.activeElement)) onLose(id);
    };
  }, [ref, id, onLose]);

  const menuAt = (row: TreeRow, x: number, y: number) => {
    onCurrent(row.info.key);
    onMenu(row, x, y, ref.current);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const active = rows[from]?.row;
    if (event.key === "Enter" && event.ctrlKey && !event.altKey && !event.metaKey && active)
      return onOpen(active, true);
    if (event.ctrlKey || event.altKey || event.metaKey || !active) return;
    const hasKids = active.hasChildren;
    const isCollapsed = collapsed.has(active.info.key);
    if (event.key === "ArrowDown") onGo(offset + from + 1);
    else if (event.key === "ArrowUp") onGo(offset + from - 1);
    else if (event.key === "Home") onGo(0);
    else if (event.key === "End") onGo(Number.MAX_SAFE_INTEGER);
    else if (event.key === "ArrowRight" && hasKids && isCollapsed) onFold(active.info.key, false);
    else if (event.key === "ArrowRight" && hasKids) onGo(offset + from + 1);
    else if (event.key === "ArrowLeft" && hasKids && !isCollapsed) onFold(active.info.key, true);
    else if (event.key === "ArrowLeft") {
      const parent = rows.findIndex(({ row }) => row.info.key === active.parent);
      if (parent >= 0) onGo(offset + parent);
    } else if (event.key === "Enter") onOpen(active);
    else if (event.key === "F2") onEdit(active);
    else if (event.key === "Delete") onRemove(active);
    else if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
      const box = document.getElementById(idOf(from))?.getBoundingClientRect();
      menuAt(active, box?.left ?? 0, box?.bottom ?? 0);
    } else return;
    event.preventDefault();
  };

  return (
    <div
      ref={ref}
      role="tree"
      aria-label={label}
      aria-activedescendant={at >= start && at < end ? idOf(at) : undefined}
      tabIndex={0}
      onKeyDown={onKeyDown}
      onFocus={(event) => {
        // The keyboard starts on the first row, so the first arrow key moves on from it.
        const first = rows[0];
        if (event.target === event.currentTarget && at < 0 && first) onCurrent(first.row.info.key);
      }}
      // The rows left out stand in as padding, which only their measured heights can give.
      style={{ paddingTop: before, paddingBottom: after }}
      className="group/tree flex flex-col focus:outline-none"
    >
      {rows.slice(start, end).map(({ row, position, siblings }, offset) => {
        const index = start + offset;
        const { info } = row;
        const isCurrent = index === at;
        const note = noteOf(row);
        const folded = collapsed.has(info.key);
        return (
          <div
            key={info.key}
            id={idOf(index)}
            data-span={info.key}
            role="treeitem"
            data-cursor-id={`tree:${info.key}`}
            aria-level={row.level}
            aria-posinset={position}
            aria-setsize={siblings}
            aria-expanded={row.hasChildren ? !folded : undefined}
            aria-busy={info.status.status === "Pending" || undefined}
            title={note || undefined}
            onClick={() => {
              // The focus first: a tree that takes it starts on its first row, which the click then overrides.
              ref.current?.focus({ preventScroll: true });
              onCurrent(info.key);
            }}
            onDoubleClick={(event) => {
              onReveal(row);
              onOpen(row, event.ctrlKey);
            }}
            onPointerEnter={(event) =>
              thumbnail.enter(pictureOf(row), event.clientX, event.clientY)
            }
            onPointerLeave={thumbnail.leave}
            onContextMenu={(event) => {
              event.preventDefault();
              menuAt(row, event.clientX, event.clientY);
            }}
            style={{
              paddingLeft: `calc(var(--spacing-m) + var(--spacing-xl) * ${row.level - 1})`,
            }}
            className={`group/row flex min-h-row shrink-0 cursor-pointer flex-col justify-center rounded-control pr-s hover:bg-hover ${
              row.dimmed || info.status.status !== "Ready" ? "text-muted" : "text-fg"
            } ${isCurrent ? "bg-hover group-focus-visible/tree:outline-(length:--stroke-focus) group-focus-visible/tree:-outline-offset-(--stroke-focus) group-focus-visible/tree:outline-accent" : ""}`}
          >
            <div className="flex h-row items-center gap-s">
              {row.hasChildren ? (
                <span
                  aria-hidden
                  onClick={(event) => {
                    event.stopPropagation();
                    onFold(info.key, !folded);
                  }}
                  onDoubleClick={(event) => event.stopPropagation()}
                >
                  <Icon name={folded ? "chevron-right" : "chevron-down"} />
                </span>
              ) : (
                <span aria-hidden className="size-icon shrink-0" />
              )}
              <Icon {...iconOf(row)} />
              <span className="min-w-0 flex-1 truncate">{info.label}</span>
              {note && <span className="sr-only">{`, ${note}`}</span>}
              {info.warnings.length > 0 && (
                <span
                  onClick={(event) => event.stopPropagation()}
                  onDoubleClick={(event) => event.stopPropagation()}
                >
                  <Popover
                    label={`${info.label}: warnings`}
                    trigger={<Icon name="triangle-alert" />}
                    className="size-row justify-center"
                  >
                    <NoticeList notices={info.warnings.map(noticeOf)} />
                  </Popover>
                </span>
              )}
              {row.target && (
                <IconButton
                  icon={info.hidden ? "eye" : "eye-off"}
                  label={`${info.hidden ? "Unhide" : "Hide"} ${info.label}`}
                  tabIndex={-1}
                  disabled={!hides}
                  onClick={(event) => {
                    event.stopPropagation();
                    onHide(row);
                  }}
                  onDoubleClick={(event) => event.stopPropagation()}
                  className={`rounded-control ${
                    info.hidden || isCurrent
                      ? ""
                      : "opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100"
                  }`}
                />
              )}
            </div>
            {info.detail && (
              <div className="flex gap-s">
                <span aria-hidden className="size-icon shrink-0" />
                <span aria-hidden className="size-icon shrink-0" />
                <span className="sr-only">, </span>
                <span className="min-w-0 truncate text-small text-muted">{info.detail}</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
