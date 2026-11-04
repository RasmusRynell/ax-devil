import { describe, expect, it } from "vite-plus/test";
import type { ItemState } from "../generated/api";
import { rowsOf } from "../session/fakeRows";
import { entry, failed, playlist, video } from "../session/fakeSession";
import { filteredOut, sectionsOf, treeOf } from "./treeModel";

const ITEMS: ItemState[] = [
  playlist("mot", [entry("mot/A", "A", ["GT"])], false),
  { ...failed("odd"), kind: "unreadable" },
  video("v", false),
  { ...failed("cam"), kind: "live" },
  video("w", false),
];

describe("sections", () => {
  const rows = rowsOf(ITEMS, [{ entry: "content-w" }]);
  const tree = treeOf({
    name: null,
    modified: false,
    recovered: false,
    epoch: 0,
    file: null,
    items: ITEMS,
    rows,
  });
  const sections = sectionsOf(tree, new Set());
  const section = (id: string) => sections.find((one) => one.id === id);

  it("lists the nonempty sections in a fixed order, each item's rows with it in the saved order", () => {
    expect(sections.map(({ title }) => title)).toEqual(["Live", "Videos", "Playlists", "Other"]);
    expect(section("videos")?.rows.map(({ info, level }) => [info.label, level])).toEqual([
      ["v.mp4", 1],
      ["v.mp4", 2],
      ["w.mp4", 1],
      ["w.mp4", 2],
    ]);
    expect(
      sectionsOf(
        tree.filter(({ item }) => item === "v"),
        new Set(),
      ).map(({ id }) => id),
    ).toEqual(["videos"]);
  });

  it("counts an item once whether or not it is hidden, and says how many rows Show hidden would add", () => {
    expect(section("videos")?.count).toBe(2);
    expect(filteredOut(section("videos")?.rows ?? [], false)).toBe(2);
    expect(filteredOut(section("videos")?.rows ?? [], true)).toBe(0);
    expect(filteredOut(section("playlists")?.rows ?? [], false)).toBe(0);
  });

  it("marks Live only while one of its streams is", () => {
    expect(section("live")?.mark).toBe("muted");
    expect(sectionsOf(tree, new Set(["cam"])).find(({ id }) => id === "live")?.mark).toBe("live");
  });
});
