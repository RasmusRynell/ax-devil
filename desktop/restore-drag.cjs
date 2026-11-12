// Electron has no startSystemMove API. On Linux, restore a maximized title drag ourselves, as the
// reference does when native movement is unavailable; normal-window dragging remains native.
const { screen } = require("electron");
const gestures = new WeakMap();

/** Only X11 permits the absolute positioning this fallback needs. Unknown backends keep native movement. */
function supportsRestoreDrag(platform, ozone, env) {
  if (platform !== "linux") return false;
  if (ozone) return ozone === "x11";
  return Boolean(env.DISPLAY && !env.WAYLAND_DISPLAY && env.XDG_SESSION_TYPE !== "wayland");
}

function cancel(window) {
  const held = gestures.get(window);
  if (!held) return;
  gestures.delete(window);
  window.removeListener("unmaximize", held.restored);
  window.removeListener("blur", held.cancel);
  window.removeListener("closed", held.cancel);
  window.webContents.removeListener("did-start-navigation", held.navigate);
  window.webContents.removeListener("render-process-gone", held.cancel);
  window.webContents.removeListener("destroyed", held.cancel);
}

function restoreDrag(window, phase, titleHeight) {
  if (phase === "end" || phase === "start") cancel(window);
  if (phase === "end") return;
  const pointer = screen.getCursorScreenPoint();
  if (phase === "start") {
    if (!window.isMaximized() || !Number.isFinite(titleHeight) || titleHeight <= 0) return;
    const bounds = window.getBounds();
    const held = {
      start: pointer,
      last: pointer,
      normal: window.getNormalBounds(),
      fraction: Math.max(0, Math.min(1, (pointer.x - bounds.x) / bounds.width)),
      titleHeight: Math.min(titleHeight * window.webContents.getZoomFactor(), bounds.height),
      restoring: false,
      ready: false,
      restored: () => {},
      cancel: () => cancel(window),
      navigate: ({ isMainFrame, isSameDocument }) => {
        if (isMainFrame && !isSameDocument) cancel(window);
      },
    };
    gestures.set(window, held);
    window.once("blur", held.cancel);
    window.once("closed", held.cancel);
    window.webContents.on("did-start-navigation", held.navigate);
    window.webContents.once("render-process-gone", held.cancel);
    window.webContents.once("destroyed", held.cancel);
    return;
  }
  const held = gestures.get(window);
  if (phase !== "move" || !held) return;
  held.last = pointer;
  const place = () => {
    if (gestures.get(window) !== held || window.isDestroyed()) return;
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
    held.restored = () => {
      if (gestures.get(window) !== held) return;
      held.ready = true;
      place();
    };
    window.once("unmaximize", held.restored);
    window.unmaximize();
  }
}
module.exports = { restoreDrag, supportsRestoreDrag };
