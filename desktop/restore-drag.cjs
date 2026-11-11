// Electron has no startSystemMove API. On Linux, restore a maximized title drag ourselves, as the
// reference does when native movement is unavailable; normal-window dragging remains native.
const { screen } = require("electron");
const gestures = new WeakMap();

function restoreDrag(window, phase, titleHeight) {
  if (phase === "end") {
    gestures.delete(window);
    return;
  }
  const pointer = screen.getCursorScreenPoint();
  if (phase === "start") {
    if (!window.isMaximized() || !Number.isFinite(titleHeight) || titleHeight <= 0) return;
    const bounds = window.getBounds();
    gestures.set(window, {
      start: pointer,
      last: pointer,
      normal: window.getNormalBounds(),
      fraction: Math.max(0, Math.min(1, (pointer.x - bounds.x) / bounds.width)),
      titleHeight: Math.min(titleHeight * window.webContents.getZoomFactor(), bounds.height),
      restoring: false,
      ready: false,
    });
    return;
  }
  const held = gestures.get(window);
  if (phase !== "move" || !held) return;
  held.last = pointer;
  const place = () => {
    if (window.isDestroyed()) return;
    window.setBounds({
      ...held.normal,
      x: Math.round(held.last.x - held.normal.width * held.fraction),
      y: Math.max(0, Math.round(held.last.y - held.titleHeight / 2)),
    });
  };
  if (held.ready) place();
  else if (
    !held.restoring &&
    Math.abs(pointer.x - held.start.x) + Math.abs(pointer.y - held.start.y) >= 4
  ) {
    held.restoring = true;
    window.once("unmaximize", () => {
      held.ready = true;
      place();
    });
    window.unmaximize();
  }
}
module.exports = { restoreDrag };
