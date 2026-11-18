import type { IconName } from "../kit/icons";
import { KIND, type Kind } from "../kit/look";
import { noticeOf } from "../kit/NoticeList";
import { MARKS } from "../live/streamState";
import { isStream } from "./rowMenu";
import { MARK_COLOR, type Placed, type RowButton } from "./tree/TreeList";
import type { TreeCan } from "./tree/treeKeys";
import { pictureUrl } from "./rowPicture";
import { tipOf, type TreeRow, type VisibleRow } from "./treeModel";

const KIND_ICONS: Record<Kind, IconName> = {
  live: "video",
  video: "square-play",
  playlist: "list-video",
  lane: "layers",
};

/** A row here can be edited, removed and given a menu; the item decides what that offers. Space does nothing. */
const CAN: TreeCan = { edit: true, remove: true, info: false, menu: true };

/**
 * The icon in front of a row and its colour: its kind's, a warning when unavailable, muted when hidden or pending; a
 * live item's is its stream's state, idle while no viewer reports one.
 */
function iconOf({ info, itemKind, stream, playlist, dimmed }: TreeRow): {
  icon: IconName;
  className: string;
} {
  const { status } = info.status;
  if (status === "Unavailable") return { icon: "triangle-alert", className: "text-caution" };
  if (info.kind === "item" && itemKind === "live" && status === "Ready") {
    const mark = MARKS[stream ?? "idle"];
    return { icon: mark.icon, className: MARK_COLOR[mark.color] };
  }
  const kind: Kind = info.kind === "lane" ? "lane" : playlist ? "playlist" : "video";
  return { icon: KIND_ICONS[kind], className: dimmed || status !== "Ready" ? "" : KIND[kind] };
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

/** The buttons of a row: a live stream's Start or, while a viewer shows it, Stop, and Hide or Unhide. */
function buttonsOf(row: TreeRow, running: boolean, hides: boolean): RowButton[] {
  const { label, hidden } = row.info;
  const buttons: RowButton[] = [];
  if (isStream(row)) {
    const verb = running ? "Stop" : "Start";
    buttons.push({
      id: verb.toLowerCase(),
      icon: running ? "square-filled" : "play",
      label: `${verb} ${label}`,
      disabled: !hides,
    });
  }
  if (row.target) {
    const verb = hidden ? "Unhide" : "Hide";
    buttons.push({
      id: "hide",
      icon: hidden ? "eye" : "eye-off",
      label: `${verb} ${label}`,
      disabled: !hides,
      plain: true,
      faint: !hidden,
    });
  }
  return buttons;
}

/**
 * A section's rows as its tree walks them. `open` holds the keys of the rows a viewer shows, `hides` whether the seat
 * may hide and unhide rows and start and stop streams, and `session` the Session whose pictures a hover shows, none
 * without the Files grant.
 */
export function placeWorkspace(
  shown: readonly VisibleRow[],
  ctx: {
    collapsed: ReadonlySet<string>;
    open: ReadonlySet<string>;
    hides: boolean;
    session: string | null;
  },
): Placed<TreeRow>[] {
  return shown.map(({ row, position, siblings }) => {
    const { info } = row;
    const running = ctx.open.has(info.key);
    return {
      key: info.key,
      parent: row.parent,
      expanded: row.hasChildren ? !ctx.collapsed.has(info.key) : null,
      can: CAN,
      level: row.level,
      position,
      siblings,
      row,
      look: {
        label: info.label,
        mark: iconOf(row),
        detail: info.detail,
        note: noteOf(row),
        title: tipOf(row, running),
        picture: pictureUrl(row, ctx.session),
        dim: row.dimmed || info.status.status !== "Ready",
        open: running,
        busy: info.status.status === "Pending",
        warnings: info.warnings.map(noticeOf),
        buttons: buttonsOf(row, running, ctx.hides),
      },
    };
  });
}
