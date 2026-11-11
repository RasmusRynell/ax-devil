const { nativeTheme } = require("electron");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const assert = require("node:assert/strict");
const { page, until, setView, frames, SCREENSHOTS } = require("./smoke-kit.cjs");
const { input, point, drag } = require("./smoke-x11.cjs");
const TITLE = "document.querySelector('[data-cursor-id=\"title:drag\"]')";

module.exports.run = async ({ window }) => {
  const driver = page(window);
  await setView(window, [800, 600], 1);
  window.setPosition(180, 180);
  await until(() => window.getBounds().x === 180, "initial window placement");
  await frames(driver);
  const from = await point(window, TITLE);
  await drag(window, from, { x: from.x + 70, y: from.y + 50 });
  await until(
    () => window.getBounds().x > 180 && window.getBounds().y > 180,
    "native title drag to move the window",
  );
  assert.deepEqual(window.getSize(), [800, 600], "moving keeps the normal size");

  // Prepare the state independently of mouse click counting; the gesture under test is restore-drag.
  window.maximize();
  await until(() => window.isMaximized(), "maximized window before restore-drag");
  await driver.wait("document.querySelector('[aria-label=Restore]')");
  const maximized = await point(window, TITLE);
  await input("move", maximized.x, maximized.y);
  await input("down");
  try {
    await input("move", maximized.x + 1, maximized.y + 1);
    await frames(driver);
    assert.ok(window.isMaximized(), "a two-pixel gesture does not restore the window");
    await input("move", maximized.x - 50, maximized.y + 80);
    await until(() => !window.isMaximized(), "dragging the maximized title to restore");
    const first = window.getBounds();
    await input("move", maximized.x - 100, maximized.y + 160);
    await until(() => window.getBounds().y > first.y, "continuing the same restore gesture");
  } finally {
    await input("up");
  }
  assert.deepEqual(window.getSize(), [800, 600], "restore drag recovers the normal size");
  const restored = window.getBounds();
  await input("move", 1200, 950);
  await frames(driver);
  assert.deepEqual(window.getBounds(), restored, "release ends the restore drag");
  assert.ok(restored.y > 0, "restore drag follows the pointer down the screen");
  const again = await point(window, TITLE);
  await drag(window, again, { x: again.x + 30, y: again.y + 30 });
  await until(() => window.getBounds().y > restored.y, "dragging again after release");

  // All eight native targets matter: a missing corner cannot be detected by testing one side.
  for (const [name, horizontal, vertical] of [
    ["west", -1, 0],
    ["east", 1, 0],
    ["north", 0, -1],
    ["south", 0, 1],
    ["northwest", -1, -1],
    ["northeast", 1, -1],
    ["southwest", -1, 1],
    ["southeast", 1, 1],
  ]) {
    window.setBounds({ x: 200, y: 200, width: 800, height: 600 });
    await until(
      () =>
        JSON.stringify(window.getBounds()) ===
        JSON.stringify({ x: 200, y: 200, width: 800, height: 600 }),
      `${name} initial bounds`,
    );
    await frames(driver);
    const at = {
      x: horizontal < 0 ? 201 : horizontal > 0 ? 998 : 600,
      y: vertical < 0 ? 201 : vertical > 0 ? 798 : 500,
    };
    await drag(window, at, { x: at.x + horizontal * 40, y: at.y + vertical * 40 });
    await until(() => {
      const bounds = window.getBounds();
      return (!horizontal || bounds.width > 800) && (!vertical || bounds.height > 600);
    }, `native ${name} resize`);
    const bounds = window.getBounds();
    if (!horizontal) assert.equal(bounds.width, 800, `${name} keeps width`);
    if (!vertical) assert.equal(bounds.height, 600, `${name} keeps height`);
    assert.ok(window.isFocused(), `${name} keeps window focus`);
  }
  for (const theme of ["light", "dark"]) {
    nativeTheme.themeSource = theme;
    await driver.wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    for (const zoom of [1, 2]) {
      await setView(window, [800, 600], zoom);
      await frames(driver);
      assert.deepEqual(
        await driver.js(`(${fit.page.toString()})()`),
        [],
        `window chrome at ${zoom * 100}%`,
      );
      await fs.writeFile(
        path.join(SCREENSHOTS, `native-window-${theme}-${zoom}.png`),
        (await window.webContents.capturePage()).toPNG(),
      );
    }
  }
  await setView(window, [800, 600], 1);
};
