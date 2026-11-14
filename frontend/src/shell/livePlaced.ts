import { ACTIONS, type Row, type Shown } from "../live/rows";
import { MARK_COLOR, type Placed } from "./tree/TreeList";

/**
 * A section's rows as its tree walks them: the shown ones, with those under an unfolded row (a search unfolds all).
 * Only the row whose info a click opened has its info lines.
 */
export function placeLive(
  shown: readonly Shown[],
  ctx: {
    collapsed: ReadonlySet<string>;
    searching: boolean;
    info: string | null;
    mayAct: boolean;
  },
): Placed<Row>[] {
  const place = (
    row: Row,
    level: number,
    parent: string | null,
    expanded: boolean | null,
    position: number,
    siblings: number,
  ): Placed<Row> => {
    const action = row.action && ACTIONS[row.action];
    return {
      key: row.id,
      parent,
      expanded,
      level,
      position,
      siblings,
      row,
      can: {
        edit: ctx.mayAct && row.menu.includes("edit"),
        remove: true,
        info: true,
        menu: row.menu.length > 0,
      },
      look: {
        label: row.label,
        mark: { icon: row.mark.icon, className: MARK_COLOR[row.mark.color] },
        tag: row.tag,
        aside: row.aside,
        detail: row.detail,
        detailWarning: row.detailWarning,
        lines: row.id === ctx.info ? row.info : [],
        title: [row.label, row.title].filter(Boolean).join("\n"),
        picture: row.thumbnail,
        dim: row.dim,
        open: row.active,
        buttons:
          row.action && action?.icon
            ? [
                {
                  id: row.action,
                  icon: action.icon,
                  label: action.tip || action.menu,
                  disabled: !ctx.mayAct,
                },
              ]
            : [],
      },
    };
  };
  return shown.flatMap(({ row, children }, at) => {
    const expanded = children.length > 0 ? ctx.searching || !ctx.collapsed.has(row.id) : null;
    return [
      place(row, 1, null, expanded, at + 1, shown.length),
      ...(expanded
        ? children.map((child, index) =>
            place(child.row, 2, row.id, null, index + 1, children.length),
          )
        : []),
    ];
  });
}
