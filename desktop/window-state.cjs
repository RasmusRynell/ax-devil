// The saved position and size of the main window. They live in a file of their own that only the shell reads: the
// device file is served to every window's page, a remote one included, and the window is this machine's.
const fs = require("node:fs");
const path = require("node:path");

const DEFAULT_SIZE = { width: 1280, height: 800 };

/** The overlapping area of `bounds` and `area`, in pixels. */
function overlap(bounds, area) {
  const across =
    Math.min(bounds.x + bounds.width, area.x + area.width) - Math.max(bounds.x, area.x);
  const down =
    Math.min(bounds.y + bounds.height, area.y + area.height) - Math.max(bounds.y, area.y);
  return across > 0 && down > 0 ? across * down : 0;
}

/** Whether the whole title-bar band, including the right-hand window controls, is on this display. */
function titleBarInside(bounds, area) {
  return (
    bounds.x >= area.x &&
    bounds.x + bounds.width <= area.x + area.width &&
    bounds.y >= area.y &&
    bounds.y + Math.min(40, bounds.height) <= area.y + area.height
  );
}

/** `bounds` moved into `area`, and shrunk to fit it when larger. */
function clampInto(bounds, area) {
  const width = Math.min(bounds.width, area.width);
  const height = Math.min(bounds.height, area.height);
  return {
    x: Math.min(Math.max(bounds.x, area.x), area.x + area.width - width),
    y: Math.min(Math.max(bounds.y, area.y), area.y + area.height - height),
    width,
    height,
  };
}

/**
 * The window options for a saved state: its bounds when the title bar is wholly on one of the `areas` (the displays' work
 * areas) and they fit it, else the bounds moved into the display they overlap most, else the default size, which Electron
 * centers. Anything that is not a state with whole-number bounds counts as nothing saved.
 */
function windowBounds(saved, areas) {
  const defaults = areas[0]
    ? {
        width: Math.min(DEFAULT_SIZE.width, areas[0].width),
        height: Math.min(DEFAULT_SIZE.height, areas[0].height),
      }
    : { ...DEFAULT_SIZE };
  const fields = ["x", "y", "width", "height"];
  if (
    !saved ||
    !fields.every((field) => Number.isInteger(saved[field])) ||
    saved.width <= 0 ||
    saved.height <= 0
  )
    return defaults;
  const bounds = { x: saved.x, y: saved.y, width: saved.width, height: saved.height };
  const holding = areas.find((area) => titleBarInside(bounds, area));
  // Keep the whole restored window in its work area, so controls also fit at larger text sizes and zoom.
  if (holding) return clampInto(bounds, holding);
  const best = areas.reduce(
    (most, area) => (overlap(bounds, area) > overlap(bounds, most) ? area : most),
    areas[0],
  );
  return best && overlap(bounds, best) > 0 ? clampInto(bounds, best) : defaults;
}

/** The state saved in `file`, or undefined when there is none or it cannot be read. */
function readWindow(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return undefined;
  }
}

/** Saves `state` in `file`, whole or not at all; a failure only costs the next start its position. */
function writeWindow(file, state) {
  const partial = `${file}.partial`;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(partial, JSON.stringify(state));
    fs.renameSync(partial, file);
  } catch (error) {
    console.error(`Could not save the window position: ${error.message}`);
  }
}

module.exports = { readWindow, windowBounds, writeWindow };
