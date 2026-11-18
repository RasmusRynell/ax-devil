import { describe, expect, it } from "vite-plus/test";
import { treeKey, type TreeCan, type TreeKeyRow } from "./treeKeys";

const CAN: TreeCan = { edit: true, remove: true, info: true, menu: true };
const row = (key: string, parent: string | null, expanded: boolean | null): TreeKeyRow => ({
  key,
  parent,
  expanded,
  can: CAN,
});
// A folded and an open parent with a child, then a leaf that can do nothing.
const ROWS = [
  row("a", null, false),
  row("b", null, true),
  row("b1", "b", null),
  { ...row("c", null, null), can: { edit: false, remove: false, info: false, menu: false } },
];

describe("treeKey", () => {
  const press = (key: string, at: number, mods: object = {}, chords = true) =>
    treeKey(
      { key, ctrlKey: false, altKey: false, metaKey: false, shiftKey: false, ...mods },
      ROWS,
      at,
      { chords },
    );

  it.each([
    ["ArrowDown", 1, {}, { move: 2 }],
    ["ArrowDown", -1, {}, { move: 0 }],
    ["ArrowUp", -1, {}, { move: 0 }],
    ["Home", 2, {}, { move: "first" }],
    ["ArrowRight", 0, {}, { fold: "a", collapse: false }],
    ["ArrowRight", 1, {}, { move: 2 }],
    ["ArrowRight", 2, {}, null],
    ["ArrowLeft", 1, {}, { fold: "b", collapse: true }],
    ["ArrowLeft", 2, {}, { move: 1 }],
    ["ArrowLeft", 0, {}, null],
    ["Enter", 0, {}, { command: "open", side: false }],
    ["Enter", 0, { ctrlKey: true }, { command: "open", side: true }],
    ["F2", 0, { altKey: true }, { command: "edit" }],
    ["Delete", 0, { ctrlKey: true }, { command: "remove" }],
    [" ", 0, {}, { command: "info" }],
    ["ContextMenu", 0, {}, { command: "menu" }],
    ["F10", 0, { shiftKey: true }, { command: "menu" }],
    // A row that can do nothing leaves these keys alone.
    ["F2", 3, {}, null],
  ])("%s on row %i with %j gives %j", (key, at, mods, effect) => {
    expect(press(key, at, mods)).toEqual(effect);
  });

  it("leaves every Ctrl, Alt and Meta key alone without chords", () => {
    for (const mods of [{ ctrlKey: true }, { altKey: true }, { metaKey: true }]) {
      for (const key of ["Enter", "F2", "Delete", "ArrowDown"])
        expect(press(key, 0, mods, false)).toBeNull();
    }
    expect(press("Enter", 0, {}, false)).toEqual({ command: "open", side: false });
  });
});
