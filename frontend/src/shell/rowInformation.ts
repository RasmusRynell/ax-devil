import { reasonOf } from "../client/errors";
import type { ContentInfo, EntryInfo, ItemRecipe, ItemState, RowInfo } from "../generated/api";
import { camerasOf } from "../live/cameras";
import { informationOf as streamInformation, SOURCES, type Information } from "../live/information";
import type { TreeRow } from "./treeModel";

type Playlist = Extract<ContentInfo, { kind: "Playlist" }>;
type Fields = Information["fields"][number][];

const info = (title: string, fields: Fields, children: Information[] = []): Information => ({
  title,
  fields,
  children,
});

/** The overlay lane `lane` of a video, with what the recipe says of its file. */
function videoLane(video: string, lane: string, recipe: ItemRecipe | undefined): Information {
  const file = recipe?.type === "Video" ? recipe.overlays.find((o) => o.label === lane) : undefined;
  const source: Fields = [
    ...(file ? [["path", file.path] as [string, string]] : []),
    ...(file?.handler ? [["handler type", file.handler] as [string, string]] : []),
  ];
  return info(lane, [
    ["type", "overlay"],
    ["video", video],
    ["lane", lane],
    ["overlay source", SOURCES.file],
    ...source,
  ]);
}

/** The playlists of `content` with the entries of each. */
function spansOf(content: Playlist) {
  let start = 0;
  return content.playlists.map((span) => {
    const entries = content.entries.slice(start, start + span.entries);
    start += span.entries;
    return { span, entries };
  });
}

/** The file of each lane the seat may read, by entry id, then lane label: the `location` of the lane's row. */
type Paths = ReadonlyMap<string, ReadonlyMap<string, string>>;

function pathsOf(rows: readonly RowInfo[]): Paths {
  const paths = new Map<string, Map<string, string>>();
  for (const { kind, entry, label, location } of rows) {
    if (kind !== "lane" || !entry || !location) continue;
    const lanes = paths.get(entry) ?? new Map<string, string>();
    paths.set(entry, lanes.set(label, location));
  }
  return paths;
}

/** An overlay lane of a playlist's entry. */
function playlistLane(
  playlist: string,
  number: number,
  entry: EntryInfo,
  at: number,
  paths: Paths,
): Information {
  const lane = entry.lanes[at];
  const path = paths.get(entry.id)?.get(lane);
  return info(lane, [
    ["type", "overlay"],
    ["playlist", playlist],
    ["entry", number],
    ["lane", `${at + 1} of ${entry.lanes.length}`],
    ["video", entry.label],
    ["overlay source", SOURCES.file],
    ...(path ? [["path", path] as [string, string]] : []),
    ["handler type", entry.handlers[at]],
    ...entry.lane_metadata[at],
  ]);
}

/** An entry of a playlist, with its lanes under it when it has overlays. */
function playlistEntry(
  playlist: string,
  number: number,
  entry: EntryInfo,
  paths: Paths,
): Information {
  return info(
    `${playlist} / Entry ${number}`,
    [
      ["type", "playlist entry"],
      ["playlist", playlist],
      ["entry", number],
      ["lanes", entry.lanes.length],
      ["videos", entry.label],
      ...entry.metadata,
    ],
    entry.handlers.map((_, at) => playlistLane(playlist, number, entry, at, paths)),
  );
}

/** The information of a row of a playlist item: one of its playlists, an entry, or a lane. */
function playlistRow(row: TreeRow, content: Playlist, paths: Paths) {
  for (const { span, entries } of spansOf(content)) {
    if (row.info.kind === "playlist" && row.info.key === span.id)
      return info(
        span.label,
        [
          ["type", "playlist"],
          ["entries", entries.length],
          ["lanes", entries.reduce((sum, { lanes }) => sum + lanes.length, 0)],
          // What the resolver says about the playlist, by name.
          ...(span.matches === undefined ? [] : [["matches", span.matches] as [string, number]]),
          ["resolver", content.display_name],
          ...(span.sequence_groups === undefined
            ? []
            : [["sequence_groups", span.sequence_groups] as [string, number]]),
          ...span.metadata,
        ],
        entries.map((entry, at) => playlistEntry(span.label, at + 1, entry, paths)),
      );
    const at = entries.findIndex(({ id }) => id === row.info.entry);
    if (at < 0 || row.info.kind === "playlist") continue;
    const lane = entries[at].lanes.indexOf(row.info.label);
    return row.info.kind === "lane" && lane >= 0
      ? playlistLane(span.label, at + 1, entries[at], lane, paths)
      : playlistEntry(span.label, at + 1, entries[at], paths);
  }
  return null;
}

/**
 * The information of what `row` stands for, as the Information dialog lists it; `null` for a row still resolving.
 * `rows` are the workspace's, which name the file of each lane.
 */
export function rowInformation(
  row: TreeRow,
  items: readonly ItemState[],
  rows: readonly RowInfo[],
): Information | null {
  const item = items.find(({ id }) => id === row.item);
  const { label, status } = row.info;
  if (!item || status.status === "Pending") return null;
  if (status.status === "Unavailable") return info(label, [["Unavailable", reasonOf(status)]]);
  if (item.resolution.status !== "Resolved") return null;
  const { content } = item.resolution;
  switch (content.kind) {
    case "LiveVideo": {
      const stream = camerasOf(items)
        .flatMap((camera) => camera.streams)
        .find(({ id }) => id === item.id);
      if (!stream) return null;
      const information = streamInformation(stream);
      return row.info.kind === "lane" ? (information.children[0] ?? null) : information;
    }
    case "SeekableVideo": {
      if (row.info.kind === "lane") return videoLane(item.label, label, item.recipe);
      // A video with no overlay file still has its own lane, which is not an overlay.
      const { recipe } = item;
      const lanes =
        recipe?.type === "Video"
          ? content.entry.lanes.filter((lane) =>
              recipe.overlays.some(({ label }) => label === lane),
            )
          : content.entry.lanes;
      return info(
        label,
        [
          ["type", "seekable video"],
          ["overlays", lanes.length],
          ...(item.recipe?.type === "Video"
            ? [["path", item.recipe.path] as [string, string]]
            : []),
        ],
        lanes.map((lane) => videoLane(label, lane, item.recipe)),
      );
    }
    case "Playlist":
      return playlistRow(row, content, pathsOf(rows));
  }
}
