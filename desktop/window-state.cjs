// The saved position and size of the main window. They live in a file of their own that only the shell reads: the
// device file is served to every window's page, a remote one included, and the window is this machine's.
const { readJson, writeJson } = require("./atomic-json.cjs");

const DEFAULT_SIZE = { width: 1280, height: 800 };

/** The overlapping area of `bounds` and `area`, in pixels. */
function overlap(bounds, area) {
  const across =
    Math.min(bounds.x + bounds.width, area.x + area.width) - Math.max(bounds.x, area.x);
  const down =
    Math.min(bounds.y + bounds.height, area.y + area.height) - Math.max(bounds.y, area.y);
  return across > 0 && down > 0 ? across * down : 0;
}

/** Whether the desktop's work-area union covers the saved rectangle, including monitor seams but not gaps.
 * Electron supplies these coordinates in DIP; applying display scale factors again would distort the union.
 */
function fullyVisible(bounds, areas) {
  const right = bounds.x + bounds.width;
  const bottom = bounds.y + bounds.height;
  const edges = [...new Set([bounds.x, right, ...areas.flatMap((a) => [a.x, a.x + a.width])])]
    .filter((x) => x >= bounds.x && x <= right)
    .sort((a, b) => a - b);
  return edges.slice(1).every((x, index) => {
    const strips = areas
      .filter((a) => a.x <= edges[index] && a.x + a.width >= x)
      .sort((a, b) => a.y - b.y);
    let covered = bounds.y;
    for (const strip of strips) {
      if (strip.y > covered) break;
      covered = Math.max(covered, strip.y + strip.height);
    }
    return covered >= bottom;
  });
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

/** Preserve fully visible saved bounds; recover unreachable bounds onto the best-overlapping display.
 * Missing or invalid bounds use a screen-bounded default size, which Electron centers.
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
  if (fullyVisible(bounds, areas)) return bounds;
  const best = areas.reduce(
    (most, area) => (overlap(bounds, area) > overlap(bounds, most) ? area : most),
    areas[0],
  );
  return best && overlap(bounds, best) > 0 ? clampInto(bounds, best) : defaults;
}

/** The state saved in `file`, or undefined when there is none or it cannot be read. */
function readWindow(file) {
  try {
    return readJson(file);
  } catch {
    return undefined;
  }
}

/** Saves `state` whole, returning null on success or the failure reason. */
function writeWindow(file, state) {
  try {
    writeJson(file, state);
    return null;
  } catch (error) {
    return `Could not save the window position: ${error.message}`;
  }
}

module.exports = { clampInto, readWindow, windowBounds, writeWindow };
