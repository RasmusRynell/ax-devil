import type { ItemState } from "../generated/api";
import type { MenuEntry } from "../kit/Menu";
import type { TreeRow } from "./treeModel";

/** What a seat may do to a row's item: change the content, and read and write the host's files. */
export type Powers = { content: boolean; host: boolean };

/** Whether the item can be edited: it was readable, and the seat is told its recipe. */
export const editable = (item: ItemState | undefined): item is ItemState =>
  item !== undefined && item.kind !== "unreadable" && item.recipe !== undefined;

/** Whether the seat may edit `item`: a video or playlist reads the host's files, a live stream is only a recipe. */
export const canEdit = (item: ItemState | undefined, powers: Powers): item is ItemState =>
  editable(item) && powers.content && (item.kind === "live" || powers.host);

/**
 * The menu of a row, in groups: Open and Open to the Side (a row that cannot open says why), Information, Hide, then Edit and Remove for
 * the whole item, which only a top-level row offers. A row still resolving opens and informs nothing, and an item
 * that was unreadable cannot be edited.
 */
export function rowMenu(row: TreeRow, item: ItemState | undefined, powers: Powers): MenuEntry[] {
  const action = (id: string, label: string, disabled = false): MenuEntry => ({
    kind: "action",
    id,
    label,
    disabled,
  });
  const { status } = row.info.status;
  const owns = row.level === 1 && item !== undefined;
  const groups: MenuEntry[][] = [
    status === "Pending"
      ? []
      : [
          action("open", "Open"),
          action("open-side", "Open to the Side"),
          action("information", "Information"),
        ],
    row.target ? [action("hidden", row.info.hidden ? "Unhide" : "Hide", !powers.content)] : [],
    owns
      ? [
          ...(editable(item) ? [action("edit", "Edit", !canEdit(item, powers))] : []),
          action("remove", "Remove", !powers.content),
        ]
      : [],
  ];
  return groups
    .filter((group) => group.length > 0)
    .flatMap((group, at) => (at === 0 ? group : [{ kind: "separator" } as const, ...group]));
}
