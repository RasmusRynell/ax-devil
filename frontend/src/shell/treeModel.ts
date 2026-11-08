import type { HiddenTarget, ItemKind, RowInfo, ViewerView, WorkspaceState } from "../generated/api";
import { casefold, compareNames } from "../casefold";
import type { CaptionMark } from "../kit/SectionCaption";
import type { StreamState, StreamStatus } from "../live/streamState";

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
  /** The state of a live item's stream while a viewer reports one; `null` for every other row. */
  stream: StreamState | null;
  /** Hidden itself or inside a hidden entry. */
  dimmed: boolean;
  /** What Hide and Unhide act on; `null` when the row cannot be hidden. */
  target: HiddenTarget | null;
  /** What the row is, which opens its hover text: `Video · 2 overlays`, `Entry 2 of 3`; empty while it has no content. */
  facts: string;
};

const counted = (count: number, one: string, many: string) =>
  `${count} ${count === 1 ? one : many}`;

/**
 * What `row` is: a video or live stream with its overlays, a playlist with its entries and how many are hidden, an
 * entry with its place among its playlist's, an overlay. `at` is the row's place among its siblings, `of` their
 * number, `inside` the rows directly under it, `overlays` how many a live stream draws.
 */
function factsOf(
  row: TreeRow,
  at: number,
  of: number,
  inside: readonly TreeRow[],
  overlays: number,
) {
  const { info, itemKind } = row;
  if (info.status.status !== "Ready") return "";
  switch (info.kind) {
    case "lane":
      return "Overlay";
    case "entry":
      return `Entry ${at + 1} of ${of}`;
    case "playlist": {
      const hidden = inside.filter((entry) => entry.info.hidden).length;
      return `Playlist · ${counted(inside.length, "entry", "entries")}${hidden ? `, ${hidden} hidden` : ""}`;
    }
    case "item": {
      if (itemKind !== "video" && itemKind !== "live") return "";
      const count = itemKind === "live" ? overlays : inside.length;
      return `${itemKind === "live" ? "Live stream" : "Video"}${count ? ` · ${counted(count, "overlay", "overlays")}` : ""}`;
    }
  }
}

/**
 * The keys of the rows the hosted `viewers` show: a video or live stream's row while a viewer shows its item, an entry's
 * row while one shows that entry, and the playlist row of such an entry. Another playlist of the same item is not open.
 */
export function openRows(
  tree: readonly TreeRow[],
  viewers: readonly Pick<ViewerView, "item" | "entry">[],
): Set<string> {
  const items = new Set(viewers.map(({ item }) => item));
  const entries = new Set(viewers.flatMap(({ entry }) => entry ?? []));
  const open = new Set<string>();
  for (const { info, parent, item } of tree) {
    if (
      info.kind === "item"
        ? items.has(item)
        : info.kind === "entry" && entries.has(info.entry ?? "")
    ) {
      open.add(info.key);
      if (info.kind === "entry" && parent !== null) open.add(parent);
    }
  }
  return open;
}

/**
 * The hover text of `row`: its whole name, what it is, why it cannot open, what resolving left out, where it is read from, and
 * whether it is hidden from playback or `open` in a viewer.
 */
export function tipOf(row: TreeRow, open: boolean): string {
  const { status, warnings, location, hidden } = row.info;
  return [
    row.info.label,
    row.facts,
    status.status === "Unavailable" ? status.reason || "Unavailable" : "",
    ...warnings.map(({ problem }) => problem),
    location ?? "",
    hidden ? "Hidden: skipped in playback" : "",
    open ? "Open in a viewer" : "",
  ]
    .filter(Boolean)
    .join("\n");
}

/** Which rows are drawn, and each one's place among its siblings for assistive technology. */
export type VisibleRow = { row: TreeRow; position: number; siblings: number };

/**
 * Adds to the backend's rows what follows from their order: each one's level and parent. A video item's lanes sit
 * directly under the item, since its one entry would only repeat it. A playlist item has no row of its own: each of
 * its playlists is a row at level 1, with its entries inside. Only a playlist's entries and the lanes of a recording
 * can be hidden, not the rows of a video, playlist or live stream.
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
    const stream = listed.kind === "item" ? streams.get(listed.item) : undefined;
    const state = stream?.text;
    const info = state
      ? { ...listed, detail: listed.detail ? `${listed.detail} · ${state}` : state }
      : listed;
    const topLevel = info.kind === "item" || info.kind === "playlist";
    const parent = topLevel ? undefined : info.kind === "entry" ? top : (entry ?? top);
    const { entry: opens } = info;
    const playlist = info.kind === "playlist";
    const itemKind = kinds.get(info.item) ?? "unreadable";
    // Only a playlist's entries and the lanes of a recording can be hidden: a live stream has no switches.
    const hideable = info.kind === "entry" || (info.kind === "lane" && itemKind !== "live");
    const row: TreeRow = {
      info,
      item: info.item,
      itemKind,
      level: (parent?.level ?? 0) + 1,
      parent: parent?.info.key ?? null,
      hasChildren: false,
      playlist,
      stream: stream?.state ?? null,
      dimmed: info.hidden || (parent?.dimmed ?? false),
      target:
        opens !== null && info.status.status === "Ready" && hideable
          ? { entry: opens, lane: info.kind === "lane" ? info.label : undefined }
          : null,
      facts: "",
    };
    if (parent) parent.hasChildren = true;
    if (topLevel) {
      top = row;
      entry = undefined;
    } else if (info.kind === "entry") entry = row;
    tree.push(row);
  }
  // A row's facts need the rows around it, which are all known only now.
  const inside = new Map<string | null, TreeRow[]>();
  for (const row of tree) {
    const siblings = inside.get(row.parent);
    if (siblings) siblings.push(row);
    else inside.set(row.parent, [row]);
  }
  const overlays = new Map(
    items.map(({ id, resolution }) => [
      id,
      resolution.status === "Resolved" &&
      resolution.content.kind === "LiveVideo" &&
      resolution.content.overlay !== "none"
        ? 1
        : 0,
    ]),
  );
  for (const siblings of inside.values())
    siblings.forEach((row, at) => {
      row.facts = factsOf(
        row,
        at,
        siblings.length,
        inside.get(row.info.key) ?? [],
        overlays.get(row.item) ?? 0,
      );
    });
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
