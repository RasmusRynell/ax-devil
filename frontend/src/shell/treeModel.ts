import type { HiddenTarget, ItemKind, RowInfo, WorkspaceState } from "../generated/api";
import { casefold, compareNames } from "../casefold";
import type { CaptionMark } from "../kit/SectionCaption";
import type { StreamStatus } from "../live/streamState";

const NO_STREAMS: ReadonlyMap<string, StreamStatus> = new Map();

/** One row of the workspace tree with what the tree needs to draw and operate it. */
export type TreeRow = {
  info: RowInfo;
  /** The workspace item the row belongs to, which a viewer is opened on. */
  item: string;
  /** What the item is, which decides the section the row is listed in. */
  itemKind: ItemKind;
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
 * A live stream's detail ends with the state of its stream (`streams`, by item) while its viewer reports one.
 */
export function treeOf(
  { rows, items }: WorkspaceState,
  streams: ReadonlyMap<string, StreamStatus> = NO_STREAMS,
): TreeRow[] {
  const kinds = new Map(items.map(({ id, kind }) => [id, kind]));
  const tree: TreeRow[] = [];
  let top: TreeRow | undefined;
  let entry: TreeRow | undefined;
  for (const listed of rows) {
    const state = listed.kind === "item" ? streams.get(listed.item)?.text : undefined;
    const info = state
      ? { ...listed, detail: listed.detail ? `${listed.detail} · ${state}` : state }
      : listed;
    const topLevel = info.kind === "item" || info.kind === "playlist";
    const parent = topLevel ? undefined : info.kind === "entry" ? top : (entry ?? top);
    const { entry: opens } = info;
    const playlist = info.kind === "playlist";
    const row: TreeRow = {
      info,
      item: info.item,
      itemKind: kinds.get(info.item) ?? "unreadable",
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

/**
 * The keys of the rows `allowed` lets through that the search leaves in: all of them without a query, else those whose
 * label and detail line contain it (case and surrounding spaces ignored), with what is inside a match and what a match
 * is inside.
 * Folds play no part.
 */
export function shownKeys(
  rows: readonly TreeRow[],
  allowed: (row: TreeRow) => boolean,
  query: string,
): Set<string> {
  const needle = casefold(query.trim());
  const inMatch = new Set<string>();
  for (const row of rows) {
    if (
      casefold(`${row.info.label} ${row.info.detail}`).includes(needle) ||
      (row.parent && inMatch.has(row.parent))
    )
      inMatch.add(row.info.key);
  }
  const shown = new Set<string>();
  const parents = new Set<string>();
  // Children follow their parent, so going backwards meets a shown child before its parent.
  for (let at = rows.length - 1; at >= 0; at--) {
    const { info, parent } = rows[at];
    if (!allowed(rows[at])) continue;
    if (needle !== "" && !inMatch.has(info.key) && !parents.has(info.key)) continue;
    shown.add(info.key);
    if (parent) parents.add(parent);
  }
  return shown;
}

/**
 * The rows to draw: those inside a collapsed row are left out, hidden ones unless `showHidden`, and those the search
 * (`query`) leaves out.
 */
export function visibleRows(
  tree: readonly TreeRow[],
  collapsed: ReadonlySet<string>,
  showHidden: boolean,
  query: string,
): VisibleRow[] {
  const allowed = shownKeys(tree, ({ dimmed }) => showHidden || !dimmed, query);
  const left = new Set<string>();
  const shown: TreeRow[] = [];
  for (const row of tree) {
    const inside = row.parent !== null && left.has(row.parent);
    if (inside || !allowed.has(row.info.key)) {
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

/** The sections of the Workspace view. */
export type SectionId = "live" | "videos" | "playlists" | "other";

/** The sections in the order they are listed, with the colour of the mark before each title. */
export const SECTIONS: readonly { id: SectionId; title: string; mark: CaptionMark }[] = [
  { id: "live", title: "Live", mark: "muted" },
  { id: "videos", title: "Videos", mark: "video" },
  { id: "playlists", title: "Playlists", mark: "playlist" },
  { id: "other", title: "Other", mark: "caution" },
];

/** The section an item of each kind is listed in; an item that could not be read is Other. */
export const SECTION_OF: Record<ItemKind, SectionId> = {
  live: "live",
  video: "videos",
  playlist: "playlists",
  unreadable: "other",
};

/** One section of the Workspace view: all its rows, hidden ones too, in the saved order. */
export type TreeSection = {
  id: SectionId;
  title: string;
  mark: CaptionMark;
  rows: TreeRow[];
  /** How many items it lists, whatever the search or Show hidden leaves out. */
  count: number;
};

/** The orders the items can be listed in; each view offers some of them. */
export const ORDERS = [
  ["workspace", "Workspace order"],
  ["name", "Name"],
  ["location", "Location"],
] as const;
export type Order = (typeof ORDERS)[number][0];

/** How two top-level rows compare in each order; 0 keeps them in the saved order. */
const COMPARE: Record<Order, (a: TreeRow, b: TreeRow) => number> = {
  workspace: () => 0,
  name: (a, b) => compareNames(a.info.label, b.info.label),
  // Rows with no location come last.
  location: ({ info: { location: a } }, { info: { location: b } }) =>
    a === null || b === null ? Number(a === null) - Number(b === null) : compareNames(a, b),
};

/**
 * Groups the tree into the `listed` sections, in their fixed order, and leaves out those with no rows. Each section
 * orders its top-level rows (`order`), each moving with what is inside it. Live is marked as live only while a stream
 * of one of its items (`live`) is.
 */
export function sectionsOf(
  tree: readonly TreeRow[],
  live: ReadonlySet<string>,
  listed: readonly SectionId[],
  order: Order,
): TreeSection[] {
  return SECTIONS.filter(({ id }) => listed.includes(id)).flatMap(({ id, title, mark }) => {
    const blocks: TreeRow[][] = [];
    for (const row of tree) {
      if (SECTION_OF[row.itemKind] !== id) continue;
      if (row.level === 1) blocks.push([row]);
      else blocks.at(-1)?.push(row);
    }
    const rows = blocks.sort(([a], [b]) => COMPARE[order](a, b)).flat();
    return rows.length === 0
      ? []
      : [
          {
            id,
            title,
            mark: id === "live" && rows.some(({ item }) => live.has(item)) ? "live" : mark,
            rows,
            count: blocks.length,
          },
        ];
  });
}

/** How many top-level rows the `listed` sections hold, whatever is hidden. */
export function listedCount(tree: readonly TreeRow[], listed: readonly SectionId[]): number {
  return tree.filter(({ level, itemKind }) => level === 1 && listed.includes(SECTION_OF[itemKind]))
    .length;
}

/** How many of `rows` Show hidden would add to the list of those the search leaves in: none while it is on. */
export function filteredOut(rows: readonly TreeRow[], showHidden: boolean, query: string): number {
  const allowed = shownKeys(rows, ({ dimmed }) => showHidden || !dimmed, query).size;
  return shownKeys(rows, () => true, query).size - allowed;
}
