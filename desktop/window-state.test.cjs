const assert = require("node:assert/strict");
const { test } = require("node:test");
const { windowBounds } = require("./window-state.cjs");

void test("saved bounds are used only when they overlap a display", () => {
  const areas = [{ x: 0, y: 0, width: 1920, height: 1080 }];
  const saved = { x: 100, y: 50, width: 900, height: 600 };
  assert.deepEqual(windowBounds(saved, areas), saved);
  assert.deepEqual(windowBounds({ ...saved, x: 2500 }, areas), { width: 1280, height: 800 });
  assert.deepEqual(windowBounds({ ...saved, width: 0 }, areas), { width: 1280, height: 800 });
  assert.deepEqual(windowBounds(undefined, areas), { width: 1280, height: 800 });
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
    height: 800,
  });
  assert.deepEqual(windowBounds({ x: -1000, y: 50, width: 1400, height: 600 }, right), {
    x: 0,
    y: 50,
    width: 1280,
    height: 600,
  });
});
