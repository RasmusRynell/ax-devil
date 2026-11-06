import { describe, expect, it } from "vite-plus/test";
import type { ItemState } from "../generated/api";
import { rowsOf } from "../session/fakeRows";
import { entry, failed, playlist, video } from "../session/fakeSession";
import { filteredOut, sectionsOf, treeOf, visibleRows, type Order } from "./treeModel";

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
    bookmarks: [],
  });
  const ALL = ["live", "videos", "playlists", "other"] as const;
  const sections = sectionsOf(tree, new Set(), ALL, "workspace");
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
        ALL,
        "workspace",
      ).map(({ id }) => id),
    ).toEqual(["videos"]);
  });

  it("counts an item once whether or not it is hidden, and says how many rows Show hidden would add", () => {
    expect(section("videos")?.count).toBe(2);
    expect(filteredOut(section("videos")?.rows ?? [], false, "")).toBe(2);
    expect(filteredOut(section("videos")?.rows ?? [], true, "")).toBe(0);
    expect(filteredOut(section("playlists")?.rows ?? [], false, "")).toBe(0);
  });

  it("marks Live only while one of its streams is", () => {
    expect(section("live")?.mark).toBe("muted");
    expect(
      sectionsOf(tree, new Set(["cam"]), ALL, "workspace").find(({ id }) => id === "live")?.mark,
    ).toBe("live");
  });

  it("lists only the sections asked for", () => {
    expect(
      sectionsOf(tree, new Set(), ["videos", "playlists"], "workspace").map(({ id }) => id),
    ).toEqual(["videos", "playlists"]);
  });
});

describe("search", () => {
  const rows = (items: ItemState[], hidden: { entry: string }[] = []) =>
    treeOf({
      name: null,
      modified: false,
      recovered: false,
      epoch: 0,
      file: null,
      items,
      rows: rowsOf(items, hidden),
      bookmarks: [],
    });
  const list = (tree: ReturnType<typeof rows>, query: string, showHidden = false) =>
    visibleRows(tree, new Set(), showHidden, query).map(({ row }) => row.info.label);

  it("ignores case and surrounding spaces, and keeps what a match is inside and what is inside a match", () => {
    const tree = rows([
      playlist("mot", [entry("mot/A", "A", ["GT"]), entry("mot/B", "B", ["DPM"])]),
    ]);
    expect(list(tree, "  dpm ")).toEqual(["mot", "B", "DPM"]);
    expect(list(tree, "B")).toEqual(["mot", "B", "DPM"]);
    expect(list(tree, "nothing")).toEqual([]);
    const folded = rows([playlist("Straße", []), playlist("οδος", [])]);
    expect(list(folded, "STRASSE")).toEqual(["Straße"]);
    expect(list(folded, "STRAẞE")).toEqual(["Straße"]);
    expect(list(folded, "οδοσ")).toEqual(["οδος"]);
    expect(list(folded, "σ")).toEqual(["οδος"]);
    const dots = rows([playlist("irmak", []), playlist("ırmak", []), playlist("İstanbul", [])]);
    expect(list(dots, "irmak")).toEqual(["irmak"]);
    expect(list(dots, "ırmak")).toEqual(["ırmak"]);
    expect(list(dots, "i̇stanbul")).toEqual(["İstanbul"]);
  });

  it("leaves hidden matches out and counts them", () => {
    const tree = rows(
      [playlist("mot", [entry("mot/A", "A", ["GT"]), entry("mot/B", "B", ["GT"])])],
      [{ entry: "mot/B" }],
    );
    expect(list(tree, "gt")).toEqual(["mot", "A", "GT"]);
    expect(filteredOut(tree, false, "gt")).toBe(2);
    expect(list(tree, "gt", true)).toEqual(["mot", "A", "GT", "B", "GT"]);
  });

  it("sorts top-level rows by name with what is inside them, equal names keeping the saved order", () => {
    const items = [
      playlist("b", [entry("b/1", "1", ["Z"]), entry("b/0", "0", ["Y"])]),
      playlist("a", [entry("a/1", "1", ["X"])]),
      playlist("B", []),
      playlist("zebra", []),
      playlist("éclair", []),
      playlist("eagle", []),
      playlist("😀", []),
      playlist("\uE000", []),
      playlist("ırmak", []),
      playlist("irmak", []),
    ];
    const names = (order: Order) =>
      sectionsOf(rows(items), new Set(), ["playlists"], order)[0].rows.map(
        ({ info }) => info.label,
      );
    expect(names("workspace")).toEqual([
      "b",
      "1",
      "Z",
      "0",
      "Y",
      "a",
      "1",
      "X",
      "B",
      "zebra",
      "éclair",
      "eagle",
      "😀",
      "\uE000",
      "ırmak",
      "irmak",
    ]);
    expect(names("name")).toEqual([
      "a",
      "1",
      "X",
      "b",
      "1",
      "Z",
      "0",
      "Y",
      "B",
      "eagle",
      "irmak",
      "zebra",
      "éclair",
      "ırmak",
      "\uE000",
      "😀",
    ]);
  });
});
