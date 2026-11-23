const assert = require("node:assert/strict");
const { test } = require("node:test");
const { shortcutInput, shortcutDisplay } = require("./shortcut-input.cjs");

void test("portable smoke chords use the physical modifiers of the tested platform", () => {
  for (const [platform, ctrl, meta] of [
    ["darwin", "meta", "control"],
    ["linux", "control", "meta"],
    ["win32", "control", "meta"],
  ]) {
    for (const [chord, keyCode, modifiers] of [
      ["Ctrl+,", ",", [ctrl]],
      ["Meta+Alt+Shift+S", "S", [meta, "alt", "shift"]],
      ["Meta+Ctrl+0", "0", [meta, ctrl]],
      ["Ctrl++", "+", [ctrl]],
      ["F2", "F2", []],
    ])
      assert.deepEqual(shortcutInput(chord, platform), { keyCode, modifiers }, platform);
  }
});

void test("smoke labels use native text on each packaged platform", () => {
  for (const [platform, expected] of [
    ["darwin", "⌘E"],
    ["linux", "Ctrl+E"],
    ["win32", "Ctrl+E"],
  ])
    assert.equal(shortcutDisplay("Ctrl+E", platform).text, expected);
});

void test("unsupported smoke platforms fail instead of silently sending the wrong shortcut", () => {
  assert.throws(() => shortcutInput("Ctrl+,", "unknown"), /Unsupported.*unknown/);
});
