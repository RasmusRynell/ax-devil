import "../client/windowBridge";

/**
 * Why the canvas gave no WebGL2 context, with the one thing to do. A browser has a graphics acceleration setting
 * to turn on; the desktop shell has none, so the driver or GPU is what blocks it.
 */
export function webgl2Missing(): string {
  return window.axDevilWindow
    ? "WebGL2 is blocked by the graphics driver or GPU. Update the driver."
    : "WebGL2 is not available. Turn on graphics acceleration in the browser settings, then reload.";
}
