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
 * Whether the row is a lane hidden itself (not one inside a hidden entry) that can be unhidden: it opens only through
 * Unhide and open. The menu and every gesture that opens a row decide by this.
 */
export const isHiddenLane = ({ info, target }: TreeRow): boolean =>
  info.kind === "lane" && info.hidden && target !== null;

/**
 * The menu of a row, in groups: Open and Open to the Side (a row that cannot open says why; a hidden lane offers Unhide
 * and open instead), Information and Export
 * video, Hide, then Edit and Remove for the whole item, which only a top-level row offers. A row still resolving
 * opens and informs nothing, and an item that was unreadable cannot be edited. A video and an entry export what a
 * viewer shows, so Export video waits for `exportable`: a viewer showing it. A viewer's header offers the same menu
 * without Edit (`withEdit` false): editing stays in the sidebar.
 */
export function rowMenu(
  row: TreeRow,
  item: ItemState | undefined,
  powers: Powers,
  exportable: boolean,
  withEdit = true,
): MenuEntry[] {
  const action = (id: string, label: string, disabled = false, reason?: string): MenuEntry => ({
    kind: "action",
    id,
    label,
    disabled,
    reason: disabled ? reason : undefined,
  });
  const { status } = row.info.status;
  const owns = row.level === 1 && item !== undefined;
  const exports =
    status === "Ready" &&
    (row.info.kind === "entry" || (row.info.kind === "item" && row.itemKind === "video"));
  const hiddenLane = isHiddenLane(row);
  const why = hiddenLane ? "Hidden lane: choose Unhide and open" : undefined;
  const groups: MenuEntry[][] = [
    status === "Pending"
      ? []
      : [
          action("open", "Open", hiddenLane, why),
          action("open-side", "Open to the Side", hiddenLane, why),
          ...(hiddenLane ? [action("unhide-open", "Unhide and open", !powers.content)] : []),
        ],
    status === "Pending"
      ? []
      : [
          action("information", "Information"),
          ...(exports
            ? [
                action(
                  "export-video",
                  "Export video",
                  !exportable || !powers.host,
                  exportable ? undefined : "Open it first",
                ),
              ]
            : []),
        ],
    row.target ? [action("hidden", row.info.hidden ? "Unhide" : "Hide", !powers.content)] : [],
    owns
      ? [
          ...(withEdit && editable(item) ? [action("edit", "Edit", !canEdit(item, powers))] : []),
          action("remove", "Remove", !powers.content),
        ]
      : [],
  ];
  return groups
    .filter((group) => group.length > 0)
    .flatMap((group, at) => (at === 0 ? group : [{ kind: "separator" } as const, ...group]));
}
