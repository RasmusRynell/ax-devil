import { Button } from "../kit/Button";
import { Chip } from "../kit/Chips";
import { Icon } from "../kit/Icon";
import { KIND } from "../kit/look";
import { ACTIONS, type Row, type RowAction } from "../live/rows";
import type { MarkColor } from "../live/streamState";

const MARK: Record<MarkColor, string> = {
  live: KIND.live,
  caution: "text-caution",
  muted: "text-muted",
};

/** A line of muted text under a row's first line, starting where its label does. */
function Line({ text, warning = false }: { text: string; warning?: boolean }) {
  return (
    <div className="flex gap-m">
      <span aria-hidden className="size-icon shrink-0" />
      <span className={`min-w-0 truncate text-small ${warning ? "text-caution" : "text-muted"}`}>
        {text}
      </span>
    </div>
  );
}

interface Props {
  row: Row;
  /** The tree's own id of the row, for `aria-activedescendant`. */
  id: string;
  level: 1 | 2;
  /** `null` for a row with nothing under it. */
  expanded: boolean | null;
  /** The row the keyboard is on. */
  current: boolean;
  /** Whether a click on it has opened its info lines. */
  infoShown: boolean;
  /** Its place among the rows that share its parent, from 1, and how many they are; the tree draws only some. */
  position: number;
  siblings: number;
  mayAct: boolean;
  onClick: () => void;
  /** A double click: the row's own open. */
  onOpen: () => void;
  onFold: () => void;
  onMenu: (x: number, y: number) => void;
  /** The pointer entered the row (where), or left it. */
  onHover: (at: { x: number; y: number } | null) => void;
  onAct: (action: RowAction) => void;
}

/**
 * One row of the Live view: its mark, tag, label and aside on a line, its one action as a button at the right, then
 * its detail and, once clicked, its info lines. The aside shortens before the label does, and the label keeps room for
 * six characters. `data-span` names the row to the tree's measurement of the rows it draws. A row open in a viewer has the accent edge. A camera with streams
 * shows an arrow in the column before it.
 */
export function LiveRow({
  row,
  id,
  level,
  expanded,
  current,
  infoShown,
  position,
  siblings,
  mayAct,
  onClick,
  onOpen,
  onFold,
  onMenu,
  onHover,
  onAct,
}: Props) {
  const { action } = row;
  const look = action && ACTIONS[action];
  return (
    <div data-span={row.id} className={`relative pl-l ${level === 2 ? "ml-l" : ""}`}>
      {expanded !== null && (
        <button
          type="button"
          tabIndex={-1}
          aria-label={`${expanded ? "Fold" : "Unfold"} ${row.label}`}
          onClick={onFold}
          className="absolute top-0 left-0 flex h-row w-l items-center justify-center text-muted"
        >
          <Icon name={expanded ? "chevron-down" : "chevron-right"} className="scale-75" />
        </button>
      )}
      <div
        id={id}
        role="treeitem"
        aria-level={level}
        aria-posinset={position}
        aria-setsize={siblings}
        aria-expanded={expanded ?? undefined}
        aria-selected={current}
        data-cursor-id={`row:${row.id}`}
        onClick={onClick}
        onDoubleClick={(event) => {
          if (!(event.target as Element).closest("button")) onOpen();
        }}
        onContextMenu={(event) => {
          if (row.menu.length === 0) return;
          event.preventDefault();
          onMenu(event.clientX, event.clientY);
        }}
        onPointerEnter={(event) => {
          if (event.pointerType === "mouse") onHover({ x: event.clientX, y: event.clientY });
        }}
        onPointerLeave={() => onHover(null)}
        className={`flex min-h-row flex-col gap-xs border-l-edge py-xs pr-m pl-m hover:bg-hover ${
          row.active ? "border-accent bg-hover" : "border-transparent"
        } ${current ? "bg-hover group-focus-visible/tree:outline-(length:--stroke-focus) group-focus-visible/tree:-outline-offset-(--stroke-focus) group-focus-visible/tree:outline-accent" : ""}`}
      >
        <div className="flex items-center gap-m">
          <Icon name={row.mark.icon} className={MARK[row.mark.color]} />
          {row.tag && <Chip>{row.tag}</Chip>}
          <span className={`min-w-title-least shrink truncate ${row.dim ? "text-muted" : ""}`}>
            {row.label}
          </span>
          <span aria-hidden className="min-w-0 flex-1" />
          {row.aside && (
            <span className="min-w-0 shrink-1000 truncate text-small text-muted">{row.aside}</span>
          )}
          {action && look?.icon && (
            <Button
              size="small"
              icon={look.icon}
              label={look.tip || look.menu}
              tabIndex={-1}
              data-cursor-id={`action:${row.id}`}
              disabled={!mayAct}
              onClick={(event) => {
                event.stopPropagation();
                onAct(action);
              }}
            />
          )}
        </div>
        {row.detail && <Line text={row.detail} warning={row.detailWarning} />}
        {infoShown && row.info.map((line) => <Line key={line} text={line} />)}
      </div>
    </div>
  );
}
