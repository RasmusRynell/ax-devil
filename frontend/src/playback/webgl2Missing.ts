/**
 * Why the canvas gave no WebGL2 context, with the one thing to do. A browser has a graphics acceleration setting to
 * turn on. Electron, which the desktop window, its hidden export renderer and the benchmark window all are, has none,
 * so the driver or GPU is what blocks it. Electron is told by its user agent: the hidden renderer has no preload bridge.
 */
export function webgl2Missing(): string {
  return /Electron\//.test(navigator.userAgent)
    ? "WebGL2 is blocked by the graphics driver or GPU. Update the driver."
    : "WebGL2 is not available. Turn on graphics acceleration in the browser settings, then reload.";
}
