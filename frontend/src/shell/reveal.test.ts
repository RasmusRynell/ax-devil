import { describe, expect, it } from "vite-plus/test";
import { scenario } from "../session/fakeScenario";
import { NO_FOLDS, revealShown, unfold, type Folds } from "./reveal";
import { treeOf } from "./treeModel";

const REVEAL = scenario("reveal");
const { ids } = REVEAL;
const treeIn = (state: string, from = REVEAL) => treeOf(from.doc(state).workspace);
const tree = treeIn("initial");
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
    const shown = revealShown(tree, show(ids.mot), closed(ids.mot, ids.other));
    expect([...shown.collapsed]).toEqual([key(ids.other)]);
    expect([...shown.revealed]).toEqual(["playlists"]);

    const moved = revealShown(tree, show(ids.v), shown);
    expect([...moved.collapsed].sort()).toEqual([key(ids.mot), key(ids.other)].sort());
    expect([...moved.revealed]).toEqual(["videos"]);
  });

  it("leaves a row the user closed while its viewer stays, and does nothing when nothing changed", () => {
    const shown = revealShown(tree, show(ids.mot), settled);
    const folded = { ...shown, collapsed: new Set([key(ids.mot)]) };
    expect(revealShown(tree, show(ids.mot), folded)).toBe(folded);
    expect(revealShown(tree, [], settled)).toBe(settled);
  });

  it("starts rows with children closed, but a playlist of at most 200 entries open, and leaves what the user opened", () => {
    const long = scenario("long-playlist");
    const first = revealShown(treeIn("initial", long), [], NO_FOLDS);
    // The playlist of 200 entries starts open; the one of 201 starts closed.
    expect([...first.collapsed]).toEqual([long.ids["p201/p201"]]);
    // Two playlists with an entry of the same name, and a recording with a lane: each entry and the recording start closed.
    const names = scenario("reveal-names");
    expect([...revealShown(treeIn("initial", names), [], NO_FOLDS).collapsed].sort()).toEqual(
      [names.ids["mot/A"], names.ids["other/A"], names.ids.v].sort(),
    );
    const opened = { ...first, collapsed: new Set<string>() };
    const updated = treeIn("added", long);
    expect(revealShown(updated, [], opened).collapsed.has(long.ids["p201/p201"])).toBe(false);
  });

  it("keeps what the user opened while the item resolves again and its rows are gone for a moment", () => {
    const first = revealShown(tree, [], NO_FOLDS);
    const opened = {
      ...first,
      collapsed: new Set([...first.collapsed].filter((k) => k !== ids["mot/A"])),
    };
    const during = revealShown(treeIn("resolving"), [], opened);
    expect(revealShown(tree, [], during).collapsed.has(ids["mot/A"])).toBe(false);
  });

  it("keeps a shown entry collapsed while its item resolves again and the entry's rows are gone for a moment", () => {
    const viewers = [{ item: ids.mot, entry: ids["mot/A"] }];
    const shown = revealShown(tree, viewers, NO_FOLDS);
    const collapsed = { ...shown, collapsed: new Set([...shown.collapsed, ids["mot/A"]]) };
    const during = revealShown(treeIn("resolving"), viewers, collapsed);
    expect(revealShown(tree, viewers, during).collapsed.has(ids["mot/A"])).toBe(true);
  });

  it("treats an entry that comes back after another resolution as newly shown, and keeps no row the tree lacks", () => {
    const at = (name: string) => [{ item: ids.mot, entry: ids[`mot/${name}`] }];
    const shown = revealShown(tree, at("A"), NO_FOLDS);
    const collapsed = { ...shown, collapsed: new Set([...shown.collapsed, ids["mot/A"]]) };
    const other = treeIn("reread-b");
    const toB = revealShown(other, at("B"), collapsed);
    expect([...toB.shown.rows].every((row) => other.some(({ info }) => info.key === row))).toBe(
      true,
    );
    expect(revealShown(treeIn("reread-a"), at("A"), toB).collapsed.has(ids["mot/A"])).toBe(false);
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
