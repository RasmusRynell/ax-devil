import { expect, it } from "vite-plus/test";
import type { ItemState } from "../generated/api";
import { rowsOf } from "../session/fakeRows";
import { entry, playlist } from "../session/fakeSession";
import { rowInformation } from "./rowInformation";
import { treeOf } from "./treeModel";

/** A playlist the resolver "Folder Pair" found one match in, whose one entry has one overlay file. */
function pairs(): ItemState {
  const item = playlist("runs", [{ ...entry("runs/e", "SEQ-01", ["GT"]), handlers: ["MOT_FILE"] }]);
  if (item.resolution.status !== "Resolved" || item.resolution.content.kind !== "Playlist")
    throw new Error("a playlist");
  const { content } = item.resolution;
  return {
    ...item,
    resolution: {
      ...item.resolution,
      content: {
        ...content,
        display_name: "Folder Pair",
        playlists: content.playlists.map((span) => ({ ...span, matches: 1 })),
      },
    },
  };
}

it("lists a playlist's own fields in order and an entry's overlay with its file and handler", () => {
  const items = [pairs()];
  const rows = rowsOf(items, []).map((row) =>
    row.kind === "lane" ? { ...row, location: "/data/gt.txt" } : row,
  );
  const tree = treeOf({
    name: null,
    modified: false,
    recovered: false,
    epoch: 0,
    file: null,
    items,
    rows,
    bookmarks: [],
  });
  const information = (kind: string) =>
    rowInformation(
      tree.find((row) => row.info.kind === kind)!,
      items,
      rows,
    )!;

  const list = information("playlist");
  expect(list.fields.map(([label]) => label)).toEqual([
    "type",
    "entries",
    "lanes",
    "matches",
    "resolver",
  ]);
  expect(list.fields.at(-1)).toEqual(["resolver", "Folder Pair"]);

  const [lane] = information("entry").children;
  expect(lane.fields).toEqual(
    expect.arrayContaining([
      ["video", "SEQ-01"],
      ["path", "/data/gt.txt"],
      ["handler type", "MOT_FILE"],
    ]),
  );
});
