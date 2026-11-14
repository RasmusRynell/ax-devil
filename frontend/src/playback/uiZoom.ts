/**
 * The page zoom factor, which overlays must not follow: a length in px stays the same size on screen when the
 * interface is zoomed. The desktop shell reads it from Chromium; a plain browser has no way to, so overlays there
 * scale with the browser's zoom. Read on every redraw, since the user can change it at any time.
 */
export function uiZoom(): number {
  return window.axDevilWindow?.zoomFactor?.() ?? 1;
}
