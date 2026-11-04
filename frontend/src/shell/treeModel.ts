import type { HiddenTarget, RowInfo, WorkspaceState } from "../generated/api";

/** One row of the workspace tree with what the tree needs to draw and operate it. */
export type TreeRow = {
  info: RowInfo;
  /** The workspace item the row belongs to, which a viewer is opened on. */
  item: string;
  /** 1 for videos and playlists, 2 and 3 for what is inside them. */
  level: number;
  /** The key of the row this one is inside; `null` at level 1. */
  parent: string | null;
  hasChildren: boolean;
  /** Whether the row is a playlist, as opposed to one video. */
  playlist: boolean;
  /** Hidden itself or inside a hidden entry. */
  dimmed: boolean;
  /** What Hide and Unhide act on; `null` when the row cannot be hidden. */
  target: HiddenTarget | null;
};

/** Which rows are drawn, and each one's place among its siblings for assistive technology. */
export type VisibleRow = { row: TreeRow; position: number; siblings: number };

/**
 * Adds to the backend's rows what follows from their order: each one's level and parent. A video item's lanes sit
 * directly under the item, since its one entry would only repeat it. A playlist item has no row of its own: each of
 * its playlists is a row at level 1, with its entries inside. A playlist row cannot be hidden, only entries and lanes.
 */
export function treeOf({ rows }: WorkspaceState): TreeRow[] {
  const tree: TreeRow[] = [];
  let top: TreeRow | undefined;
  let entry: TreeRow | undefined;
  for (const info of rows) {
    const topLevel = info.kind === "item" || info.kind === "playlist";
    const parent = topLevel ? undefined : info.kind === "entry" ? top : (entry ?? top);
    const { entry: opens } = info;
    const playlist = info.kind === "playlist";
    const row: TreeRow = {
      info,
      item: info.item,
      level: (parent?.level ?? 0) + 1,
      parent: parent?.info.key ?? null,
      hasChildren: false,
      playlist,
      dimmed: info.hidden || (parent?.dimmed ?? false),
      target:
        opens !== null && info.status.status === "Ready" && !playlist
          ? { entry: opens, lane: info.kind === "lane" ? info.label : undefined }
          : null,
    };
    if (parent) parent.hasChildren = true;
    if (topLevel) {
      top = row;
      entry = undefined;
    } else if (info.kind === "entry") entry = row;
    tree.push(row);
  }
  return tree;
}

/** The rows to draw: those inside a collapsed row are left out, hidden ones unless `showHidden`. */
export function visibleRows(
  tree: readonly TreeRow[],
  collapsed: ReadonlySet<string>,
  showHidden: boolean,
): VisibleRow[] {
  const left = new Set<string>();
  const shown: TreeRow[] = [];
  for (const row of tree) {
    const inside = row.parent !== null && left.has(row.parent);
    if (inside || (row.dimmed && !showHidden)) {
      left.add(row.info.key);
      continue;
    }
    if (collapsed.has(row.info.key)) left.add(row.info.key);
    shown.push(row);
  }
  const counts = new Map<string | null, number>();
  for (const row of shown) counts.set(row.parent, (counts.get(row.parent) ?? 0) + 1);
  const seen = new Map<string | null, number>();
  return shown.map((row) => {
    const position = (seen.get(row.parent) ?? 0) + 1;
    seen.set(row.parent, position);
    return { row, position, siblings: counts.get(row.parent) ?? 1 };
  });
}
