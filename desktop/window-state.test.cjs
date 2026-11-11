const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { readWindow, windowBounds, writeWindow } = require("./window-state.cjs");

void test("a saved window survives a restart, and a missing file saves nothing", () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-devil-window-"));
  try {
    const file = path.join(folder, "window.json");
    assert.equal(readWindow(file), undefined);
    const saved = { x: 100, y: 40, width: 1000, height: 700, maximized: true };
    writeWindow(file, saved);
    assert.deepEqual(readWindow(file), saved);
  } finally {
    fs.rmSync(folder, { recursive: true, force: true });
  }
});

void test("a saved file of the wrong shape opens the window at the default size", () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "ax-devil-window-"));
  try {
    const file = path.join(folder, "window.json");
    for (const text of [
      "null",
      "[]",
      "7",
      '{"x":"10","y":0,"width":9,"height":9}',
      '{"x":1,"y":1,"height":9}',
    ]) {
      fs.writeFileSync(file, text);
      assert.deepEqual(
        windowBounds(readWindow(file), [{ x: 0, y: 0, width: 1920, height: 1080 }]),
        {
          width: 1280,
          height: 800,
        },
      );
    }
  } finally {
    fs.rmSync(folder, { recursive: true, force: true });
  }
});

void test("saved bounds are used only when they overlap a display", () => {
  const areas = [{ x: 0, y: 0, width: 1920, height: 1080 }];
  const saved = { x: 100, y: 50, width: 900, height: 600 };
  assert.deepEqual(windowBounds(saved, areas), saved);
  assert.deepEqual(windowBounds({ ...saved, x: 2500 }, areas), { width: 1280, height: 800 });
  assert.deepEqual(windowBounds({ ...saved, width: 0 }, areas), { width: 1280, height: 800 });
  assert.deepEqual(windowBounds(undefined, areas), { width: 1280, height: 800 });
  // Electron work areas are already in DIP, including displays with different scale factors.
  for (const [name, displays, spanning, expected] of [
    [
      "adjacent",
      [...areas, { x: 1920, y: 0, width: 1920, height: 1080 }],
      { x: 1500, y: 100, width: 900, height: 600 },
    ],
    [
      "stacked",
      [...areas, { x: 0, y: 1080, width: 1920, height: 1080 }],
      { x: 100, y: 900, width: 900, height: 600 },
    ],
    [
      "mixed scale",
      [
        { ...areas[0], scaleFactor: 1 },
        { x: 1920, y: 0, width: 1280, height: 720, scaleFactor: 2 },
      ],
      { x: 1500, y: 100, width: 1500, height: 600 },
    ],
    [
      "gap",
      [...areas, { x: 2020, y: 0, width: 1920, height: 1080 }],
      { x: 1500, y: 100, width: 900, height: 600 },
      { x: 1020, y: 100, width: 900, height: 600 },
    ],
  ])
    assert.deepEqual(windowBounds(spanning, displays), expected ?? spanning, name);
  // The title bar is visible, but the window is larger than the display: it is shrunk and moved onto it.
  assert.deepEqual(windowBounds({ x: 100, y: 50, width: 4000, height: 3000 }, areas), {
    x: 0,
    y: 0,
    width: 1920,
    height: 1080,
  });
});

void test("bounds with the title bar off screen are moved into the display they overlap most", () => {
  const areas = [{ x: 0, y: 0, width: 1920, height: 1080 }];
  // The monitor above is gone: 10 px of the window still overlaps, but its title bar does not.
  assert.deepEqual(windowBounds({ x: 100, y: -790, width: 1280, height: 800 }, areas), {
    x: 100,
    y: 0,
    width: 1280,
    height: 800,
  });
  // Title bar past the right edge, and larger than the display.
  assert.deepEqual(windowBounds({ x: 1800, y: 100, width: 2400, height: 1200 }, areas), {
    x: 0,
    y: 0,
    width: 1920,
    height: 1080,
  });
  // The left monitor is gone: the window moves to the remaining one on the right.
  const right = [{ x: 0, y: 0, width: 1280, height: 720 }];
  assert.deepEqual(windowBounds({ x: -1500, y: 50, width: 1400, height: 600 }, right), {
    width: 1280,
    height: 720,
  });
  assert.deepEqual(windowBounds({ x: -1000, y: 50, width: 1400, height: 600 }, right), {
    x: 0,
    y: 50,
    width: 1280,
    height: 600,
  });
});
