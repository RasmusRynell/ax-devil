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
const tree = treeOf({
  name: null,
  modified: false,
  recovered: false,
  epoch: 0,
  file: null,
  items: ITEMS,
  rows: rowsOf(ITEMS, []),
  bookmarks: [],
});
const key = (item: string) =>
  tree.find((row) => row.item === item && row.level === 1)?.info.key ?? "";
const closed = (...items: string[]): Folds => ({ ...NO_FOLDS, collapsed: new Set(items.map(key)) });

describe("what a viewer shows", () => {
  it("reveals the shown item's row and its section, and closes the row once the viewer moves on", () => {
    const shown = revealShown(tree, "mot", closed("mot", "other"));
    expect([...shown.collapsed]).toEqual([key("other")]);
    expect([...shown.revealed]).toEqual(["playlists"]);

    const moved = revealShown(tree, "v", shown);
    expect([...moved.collapsed].sort()).toEqual([key("mot"), key("other")].sort());
    expect([...moved.revealed]).toEqual(["videos"]);
  });

  it("leaves a row the user closed while its viewer stays, and does nothing when nothing changed", () => {
    const shown = revealShown(tree, "mot", NO_FOLDS);
    const folded = { ...shown, collapsed: new Set([key("mot")]) };
    expect(revealShown(tree, "mot", folded)).toBe(folded);
    expect(revealShown(tree, undefined, NO_FOLDS)).toBe(NO_FOLDS);
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
