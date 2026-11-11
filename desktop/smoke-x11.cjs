// Native input must enter X11, not webContents.sendInputEvent (which bypasses the window manager).
const { execFile } = require("node:child_process");
const path = require("node:path");
const { promisify } = require("node:util");
const { SMOKE_ROOT, settled, frames, page } = require("./smoke-kit.cjs");
const execute = promisify(execFile);

async function input(action, ...values) {
  if (process.env.AX_DEVIL_PRIVATE_X11 !== "1")
    throw new Error("Native window tests require scripts/isolated --window-manager");
  await execute("python3", [
    path.join(SMOKE_ROOT, "scripts/private-x11.py"),
    action,
    ...values.map(String),
  ]);
}

async function point(window, selector) {
  const at = await settled(
    window,
    `(() => {
    const r = (${selector}).getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  })()`,
  );
  const bounds = window.getContentBounds();
  const zoom = window.webContents.getZoomFactor();
  return { x: Math.round(bounds.x + at.x * zoom), y: Math.round(bounds.y + at.y * zoom) };
}

async function drag(window, from, to) {
  await input("move", from.x, from.y);
  await input("down");
  try {
    await frames(page(window));
    for (const fraction of [0.25, 0.5, 0.75, 1]) {
      await input(
        "move",
        Math.round(from.x + (to.x - from.x) * fraction),
        Math.round(from.y + (to.y - from.y) * fraction),
      );
      await frames(page(window));
    }
  } finally {
    await input("up");
  }
}
module.exports = { input, point, drag };
