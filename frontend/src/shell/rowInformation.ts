import { reasonOf } from "../errors";
import type { ContentInfo, EntryInfo, ItemRecipe, ItemState } from "../generated/api";
import { camerasOf } from "../live/cameras";
import { informationOf as streamInformation, type Information } from "../live/information";
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
    ["overlay source", "file"],
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

/** An overlay lane of a playlist's entry. */
const playlistLane = (playlist: string, number: number, entry: EntryInfo, at: number) =>
  info(entry.lanes[at], [
    ["type", "overlay"],
    ["playlist", playlist],
    ["entry", String(number)],
    ["lane", `${at + 1} of ${entry.lanes.length}`],
  ]);

/** An entry of a playlist, with its lanes under it when it has several. */
function playlistEntry(playlist: string, number: number, entry: EntryInfo): Information {
  return info(
    `${playlist} / Entry ${number}`,
    [
      ["type", "playlist entry"],
      ["playlist", playlist],
      ["entry", String(number)],
      ["lanes", String(entry.lanes.length)],
    ],
    entry.lanes.length > 1
      ? entry.lanes.map((_, at) => playlistLane(playlist, number, entry, at))
      : [],
  );
}

/** The information of a row of a playlist item: one of its playlists, an entry, or a lane. */
function playlistRow(row: TreeRow, content: Playlist, recipe: ItemRecipe | undefined) {
  for (const { span, entries } of spansOf(content)) {
    if (row.info.kind === "playlist" && row.info.key === span.id)
      return info(
        span.label,
        [
          ["type", "playlist"],
          ...(recipe?.type === "Playlist" ? [["source", recipe.resolver] as [string, string]] : []),
          ["entries", String(entries.length)],
          ["lanes", String(entries.reduce((sum, { lanes }) => sum + lanes.length, 0))],
        ],
        entries.map((entry, at) => playlistEntry(span.label, at + 1, entry)),
      );
    const at = entries.findIndex(({ id }) => id === row.info.entry);
    if (at < 0 || row.info.kind === "playlist") continue;
    const lane = entries[at].lanes.indexOf(row.info.label);
    return row.info.kind === "lane" && lane >= 0
      ? playlistLane(span.label, at + 1, entries[at], lane)
      : playlistEntry(span.label, at + 1, entries[at]);
  }
  return null;
}

/** The information of what `row` stands for, as the Information dialog lists it; `null` for a row still resolving. */
export function rowInformation(row: TreeRow, items: readonly ItemState[]): Information | null {
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
      return stream ? streamInformation(stream) : null;
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
          ["overlays", String(lanes.length)],
          ...(item.recipe?.type === "Video"
            ? [["path", item.recipe.path] as [string, string]]
            : []),
        ],
        lanes.map((lane) => videoLane(label, lane, item.recipe)),
      );
    }
    case "Playlist":
      return playlistRow(row, content, item.recipe);
  }
}
