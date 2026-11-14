import { describe, expect, it } from "vite-plus/test";
import { scenario } from "../session/fakeScenario";
import { filteredOut, sectionsOf, tipOf, treeOf, visibleRows, type Order } from "./treeModel";

const KINDS = scenario("tree-kinds");
const { ids } = KINDS;
const tree = treeOf(KINDS.doc().workspace);

describe("sections", () => {
  const ALL = ["live", "videos", "playlists", "other"] as const;
  const sections = sectionsOf(tree, new Set(), ALL, "workspace");
  const section = (id: string) => sections.find((one) => one.id === id);

  it("lists the nonempty sections in a fixed order, each item's rows with it in the saved order", () => {
    expect(sections.map(({ title }) => title)).toEqual(["Live", "Videos", "Playlists", "Other"]);
    expect(section("videos")?.rows.map(({ info, level }) => [info.label, level])).toEqual([
      ["v.mp4", 1],
      ["GT", 2],
      ["bad.mp4", 1],
      ["wait.mp4", 1],
      ["w.mp4", 1],
    ]);
    expect(
      sectionsOf(
        tree.filter(({ item }) => item === ids.w),
        new Set(),
        ALL,
        "workspace",
      ).map(({ id }) => id),
    ).toEqual(["videos"]);
  });

  it("counts an item once whether or not it is hidden, and says how many rows Show hidden would add", () => {
    expect(section("playlists")?.count).toBe(1);
    expect(filteredOut(section("playlists")?.rows ?? [], false, "")).toBe(3);
    expect(filteredOut(section("playlists")?.rows ?? [], true, "")).toBe(0);
    expect(filteredOut(section("videos")?.rows ?? [], false, "")).toBe(0);
  });

  it("marks Live only while one of its streams is", () => {
    expect(section("live")?.mark).toBe("muted");
    expect(
      sectionsOf(tree, new Set([ids.cam]), ALL, "workspace").find(({ id }) => id === "live")?.mark,
    ).toBe("live");
  });

  it("lists only the sections asked for", () => {
    expect(
      sectionsOf(tree, new Set(), ["videos", "playlists"], "workspace").map(({ id }) => id),
    ).toEqual(["videos", "playlists"]);
  });
});

describe("hide targets", () => {
  it("lets only a playlist's entries and the lanes of a recording be hidden", () => {
    const hideable = tree.filter(({ target }) => target).map(({ info }) => info);
    const kindsOf = (item: string) =>
      hideable.filter((info) => info.item === item).map(({ kind }) => kind);
    expect(new Set(hideable.map(({ item }) => item))).toEqual(new Set([ids.v, ids.mot]));
    expect(kindsOf(ids.v)).toEqual(["lane"]);
    expect(kindsOf(ids.mot)).toEqual(["entry", "lane", "lane", "entry", "lane"]);
  });
});

describe("hover text", () => {
  const tip = (item: string, kind: string, open = false) =>
    tipOf(
      tree.find(({ info }) => info.item === item && info.kind === kind)!,
      open,
    ).split("\n");

  it("says what a row is, counting the playlist's hidden entries and numbering the entries", () => {
    expect(tip(ids.mot, "playlist")).toContain("Playlist · 2 entries, 1 hidden");
    expect(tree.filter(({ info }) => info.kind === "entry").map(({ facts }) => facts)).toEqual([
      "Entry 1 of 2",
      "Entry 2 of 2",
    ]);
    expect(tip(ids.v, "item")).toEqual(["v.mp4", "Video · 1 overlay", "/data/v/v.mp4"]);
    expect(tip(ids.v, "lane")).toContain("Overlay");
  });

  it("adds why a row cannot open, that it is hidden, and that a viewer shows it", () => {
    expect(tip(ids.bad, "item")).toEqual(["bad.mp4", "File not found"]);
    const hidden = tree.find(({ info }) => info.hidden)!;
    expect(tipOf(hidden, true).split("\n").slice(-2)).toEqual([
      "Hidden: skipped in playback",
      "Open in a viewer",
    ]);
  });
});

describe("search", () => {
  const SORT = scenario("tree-sort");
  const sorted = treeOf(SORT.doc().workspace);
  const list = (rows: typeof tree, query: string, showHidden = false) =>
    visibleRows(rows, new Set(), showHidden, query).map(({ row }) => row.info.label);

  it("ignores case and surrounding spaces, and keeps what a match is inside and what is inside a match", () => {
    expect(list(sorted, "  y ")).toEqual(["b", "0", "Y"]);
    expect(list(sorted, "0")).toEqual(["b", "0", "Y"]);
    expect(list(sorted, "nothing")).toEqual([]);
    expect(list(sorted, "STRASSE")).toEqual(["Straße", "1"]);
    expect(list(sorted, "STRAẞE")).toEqual(["Straße", "1"]);
    expect(list(sorted, "οδοσ")).toEqual(["οδος", "1"]);
    expect(list(sorted, "σ")).toEqual(["οδος", "1"]);
    expect(list(sorted, "irmak")).toEqual(["irmak", "1"]);
    expect(list(sorted, "ırmak")).toEqual(["ırmak", "1"]);
    expect(list(sorted, "i̇stanbul")).toEqual(["İstanbul", "1"]);
  });

  it("matches the detail line that tells same-named rows apart", () => {
    const places = treeOf(scenario("tree-locations").doc().workspace);
    expect(visibleRows(places, new Set(), false, "Q").map(({ row }) => row.info.detail)).toEqual([
      "q",
    ]);
  });

  it("ends a live stream's detail with its stream's state, which the search matches", () => {
    const { workspace } = KINDS.doc();
    const states = new Map([[ids.cam, { state: "live", text: "Live", anyLive: true } as const]]);
    const detail = (rows: typeof tree, item: string) =>
      rows.find(({ info }) => info.item === item && info.kind === "item")?.info.detail;
    expect(detail(treeOf(workspace, states), ids.cam)).toBe("10.0.0.5 · Live");
    expect(detail(treeOf(workspace), ids.cam)).toBe("10.0.0.5");
    expect(detail(treeOf(workspace, states), ids.dock)).toBe("10.0.0.6");
    expect(
      treeOf(workspace, states)
        .filter(({ stream }) => stream)
        .map(({ item }) => item),
    ).toEqual([ids.cam]);
    const found = visibleRows(treeOf(workspace, states), new Set(), false, "· live");
    expect(new Set(found.map(({ row }) => row.item))).toEqual(new Set([ids.cam]));
  });

  it("leaves hidden matches out and counts them", () => {
    expect(list(tree, "gt")).toEqual(["mot", "A", "GT", "v.mp4", "GT"]);
    expect(filteredOut(tree, false, "gt")).toBe(2);
    expect(list(tree, "gt", true)).toEqual(["mot", "A", "GT", "B", "GT", "v.mp4", "GT"]);
  });

  it("sorts top-level rows by name with what is inside them, equal names keeping the saved order", () => {
    const names = (order: Order) =>
      sectionsOf(sorted, new Set(), ["playlists"], order)[0].rows.map(({ info }) => info.label);
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
      "1",
      "zebra",
      "1",
      "éclair",
      "1",
      "eagle",
      "1",
      "😀",
      "1",
      "\uE000",
      "1",
      "ırmak",
      "1",
      "irmak",
      "1",
      "İstanbul",
      "1",
      "Straße",
      "1",
      "οδος",
      "1",
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
      "1",
      "eagle",
      "1",
      "irmak",
      "1",
      "İstanbul",
      "1",
      "Straße",
      "1",
      "zebra",
      "1",
      "éclair",
      "1",
      "ırmak",
      "1",
      "οδος",
      "1",
      "\uE000",
      "1",
      "😀",
      "1",
    ]);
  });

  it("sorts top-level rows by location, ignoring case, with rows that have none last in the saved order", () => {
    const places = treeOf(scenario("tree-locations").doc().workspace);
    const order = sectionsOf(places, new Set(), ["videos"], "location")[0].rows.map(
      ({ info }) => info.label,
    );
    expect(order).toEqual([
      "a.mp4",
      "B.mp4",
      "c.mp4",
      "same.mp4",
      "same.mp4",
      "none.mp4",
      "also-none.mp4",
    ]);
  });
});
