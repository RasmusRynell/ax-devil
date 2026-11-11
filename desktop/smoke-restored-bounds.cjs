const { screen } = require("electron");
const fs = require("node:fs/promises");
const path = require("node:path");
const assert = require("node:assert/strict");
const { openWindow } = require("./window.cjs");
const { windowBounds } = require("./window-state.cjs");
const { page, until, frames, SMOKE_ROOT } = require("./smoke-kit.cjs");
const { input, point } = require("./smoke-x11.cjs");

module.exports.run = async ({ window }) => {
  const shots = path.join(SMOKE_ROOT, "artifacts/window-behavior/shots");
  await fs.mkdir(shots, { recursive: true });
  let index = 0;
  const area = screen.getPrimaryDisplay().workArea;
  // Gone monitor, oversized window, and a visible left title strip with the controls beyond the right edge.
  for (const saved of [
    { x: area.x + area.width * 2, y: area.y, width: 900, height: 600 },
    { x: area.x + 100, y: area.y + 50, width: area.width * 2, height: area.height * 2 },
    { x: area.x + area.width - 300, y: area.y + 50, width: 800, height: 600 },
    { x: area.x + 100, y: area.y + area.height - 45, width: 800, height: 600 },
  ]) {
    const restored = await openWindow(new URL(window.webContents.getURL()).origin, {
      bounds: windowBounds(saved, [area]),
      fatal: (error) => {
        throw error;
      },
    });
    try {
      restored.webContents.setZoomFactor(2);
      const driver = page(restored);
      await driver.wait("document.querySelector('[aria-label=Close]')");
      for (const label of ["Minimize", "Maximize", "Close"]) {
        const at = await point(
          restored,
          `document.querySelector('[aria-label=${label}]${label === "Maximize" ? ", [aria-label=Restore]" : ""}')`,
        );
        assert.ok(
          at.x >= area.x &&
            at.x < area.x + area.width &&
            at.y >= area.y &&
            at.y < area.y + area.height,
          `${label} is on screen for ${JSON.stringify(saved)}`,
        );
      }
      await frames(driver);
      await fs.writeFile(
        path.join(shots, `restored-bounds-${++index}.png`),
        (await restored.webContents.capturePage()).toPNG(),
      );
      const close = await point(restored, "document.querySelector('[aria-label=Close]')");
      await input("move", close.x, close.y);
      try {
        await input("down");
      } finally {
        await input("up");
      }
      await until(
        () => restored.isDestroyed(),
        "the restored Close control to work through native input",
      );
    } finally {
      if (!restored.isDestroyed()) restored.destroy();
    }
  }
};
