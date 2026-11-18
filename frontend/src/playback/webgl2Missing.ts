/**
 * Why the canvas gave no WebGL2 context, and what to switch on, as paragraphs separated by blank lines.
 * A browser has a graphics acceleration setting. Electron, which the desktop window, its hidden export renderer and
 * the benchmark window all are, has none, so the driver or GPU is what blocks it. Electron is told by its user agent:
 * the hidden renderer has no preload bridge.
 */
export function webgl2Missing(): string {
  return (
    /Electron\//.test(navigator.userAgent)
      ? [
          "ax-devil draws video with WebGL2, which needs the GPU.",
          "The graphics driver or GPU blocks it: update the graphics driver.",
          "A machine or session without a GPU cannot draw video.",
        ]
      : [
          "ax-devil draws video with WebGL2, which needs the browser's graphics (GPU) acceleration.",
          "Chrome, Brave, Edge and other Chromium browsers: turn on Settings > System > Use graphics acceleration when available, then Relaunch and reload this page.",
          "Firefox: untick Settings > General > Performance > Use recommended performance settings, tick Use hardware acceleration when available, then restart.",
          "Already on? The browser has blocked this GPU: update the graphics driver. A remote desktop session or a virtual machine without a GPU cannot draw video; use a local session.",
        ]
  ).join("\n\n");
}
