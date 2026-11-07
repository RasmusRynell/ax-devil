import { describe, expect, it } from "vite-plus/test";
import type { ItemState } from "../generated/api";
import { rowsOf } from "../session/fakeRows";
import { entry, playlist, video } from "../session/fakeSession";
import { NO_FOLDS, revealShown, unfold, type Folds } from "./reveal";
import { treeOf } from "./treeModel";

const ITEMS: ItemState[] = [
  playlist("mot", [entry("mot/A", "A", ["GT"])], false),
  playlist("other", [entry("other/A", "B", ["GT"])], false),
  video("v", false),
];
const treeFor = (items: ItemState[]) =>
  treeOf({
    name: null,
    modified: false,
    recovered: false,
    epoch: 0,
    file: null,
    items,
    rows: rowsOf(items, []),
    bookmarks: [],
  });
const tree = treeFor(ITEMS);
const key = (item: string) =>
  tree.find((row) => row.item === item && row.level === 1)?.info.key ?? "";
// Folds that have already given every row with children its first fold, and so leave the rest to the user.
const settled: Folds = {
  ...NO_FOLDS,
  known: new Set(tree.filter(({ hasChildren }) => hasChildren).map(({ info }) => info.key)),
};
const show = (...items: string[]) => items.map((item) => ({ item }));
const closed = (...items: string[]): Folds => ({ ...settled, collapsed: new Set(items.map(key)) });

describe("what a viewer shows", () => {
  it("reveals the shown item's row and its section, and closes the row once the viewer moves on", () => {
    const shown = revealShown(tree, show("mot"), closed("mot", "other"));
    expect([...shown.collapsed]).toEqual([key("other")]);
    expect([...shown.revealed]).toEqual(["playlists"]);

    const moved = revealShown(tree, show("v"), shown);
    expect([...moved.collapsed].sort()).toEqual([key("mot"), key("other")].sort());
    expect([...moved.revealed]).toEqual(["videos"]);
  });

  it("leaves a row the user closed while its viewer stays, and does nothing when nothing changed", () => {
    const shown = revealShown(tree, show("mot"), settled);
    const folded = { ...shown, collapsed: new Set([key("mot")]) };
    expect(revealShown(tree, show("mot"), folded)).toBe(folded);
    expect(revealShown(tree, [], settled)).toBe(settled);
  });

  it("starts rows with children closed, but a playlist of at most 200 entries open, and leaves what the user opened", () => {
    const playlistOf = (count: number) =>
      playlist(
        "p",
        Array.from({ length: count }, (_, at) => entry(`p/${at}`, `${at}`, [])),
        false,
      );
    expect([...revealShown(tree, [], NO_FOLDS).collapsed].sort()).toEqual(
      ["mot/A", "other/A", "v"].sort(),
    );
    expect(revealShown(treeFor([playlistOf(200)]), [], NO_FOLDS).collapsed.size).toBe(0);

    const long = treeFor([playlistOf(201)]);
    const first = revealShown(long, [], NO_FOLDS);
    expect([...first.collapsed]).toEqual(["p/p"]);
    const opened = { ...first, collapsed: new Set<string>() };
    const updated = treeFor([playlistOf(201), video("v", false)]);
    expect(revealShown(updated, [], opened).collapsed.has("p/p")).toBe(false);
  });

  it("keeps what the user opened while the item resolves again and its rows are gone for a moment", () => {
    const first = revealShown(tree, [], NO_FOLDS);
    const opened = {
      ...first,
      collapsed: new Set([...first.collapsed].filter((k) => k !== "mot/A")),
    };
    const resolving = treeFor([
      { ...ITEMS[0], resolution: { status: "Pending" } },
      ...ITEMS.slice(1),
    ]);
    const during = revealShown(resolving, [], opened);
    expect(revealShown(tree, [], during).collapsed.has("mot/A")).toBe(false);
  });

  it("unfolds a row with the rows above it", () => {
    const entryRow = tree.find(({ level }) => level === 2)?.info.key ?? "";
    const all = new Set(tree.map(({ info }) => info.key));
    const open = unfold(all, tree, entryRow);
    expect(open.has(entryRow)).toBe(false);
    expect(open.size).toBe(all.size - 2);
    expect(unfold(open, tree, entryRow)).toBe(open);
  });
});
