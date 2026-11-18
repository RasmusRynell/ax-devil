const { nativeTheme } = require("electron");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const assert = require("node:assert/strict");
const { page, until, setView, frames, SCREENSHOTS } = require("./smoke-kit.cjs");
const { openWindow } = require("./window.cjs");
const { input, point, drag, focus } = require("./smoke-x11.cjs");
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

  // Real clicks must retain their title/button target while the maximized fallback captures a pointer.
  const click = async (target, selector, action = "click") => {
    await focus(target);
    await input("move", 1200, 950);
    const at = await point(target, selector);
    await input("move", at.x, at.y);
    await frames(page(target));
    await input(action);
  };
  window.maximize();
  await until(() => window.isMaximized(), "maximized before native title clicks");
  await driver.wait("document.querySelector('[aria-label=Restore]')");
  const heldBounds = window.getBounds();
  await click(window, TITLE);
  await frames(driver);
  assert.ok(window.isMaximized(), "a stationary title click does not restore");
  assert.deepEqual(window.getBounds(), heldBounds, "a stationary title click does not move");
  for (const label of ["File", "View", "Help"]) {
    const menu = `[...document.querySelectorAll('[data-top]')].find(b => b.textContent === '${label}')`;
    await click(window, menu);
    await driver.wait("document.querySelector('[role=menu]')");
    await click(window, menu);
    await driver.wait("!document.querySelector('[role=menu]')");
    assert.ok(window.isMaximized(), `${label} stays an ordinary button while maximized`);
  }
  await click(window, "document.querySelector('[data-cursor-id=share]')");
  await driver.wait(`document.querySelector('aside input[aria-label="Join link"]')`);
  await click(window, "document.querySelector('header [aria-label=Session]')");
  await driver.wait("document.querySelector('[role=menu]')");
  await click(
    window,
    "[...document.querySelectorAll('[role=menuitem]')].find(b => b.textContent === 'Stop sharing')",
  );
  await driver.wait("!document.querySelector('header [aria-label=Session]')");
  await driver.js("document.querySelector('[aria-label=Workspace]').click()");
  await click(window, "document.querySelector('[aria-label=Restore]')");
  await until(() => !window.isMaximized(), "native Restore button while maximized");
  await click(window, "document.querySelector('[aria-label=Maximize]')");
  await until(() => window.isMaximized(), "native Maximize button");
  await click(window, "document.querySelector('[aria-label=Minimize]')");
  await until(() => window.isMinimized(), "native Minimize button while maximized");
  window.restore();
  await until(() => !window.isMinimized(), "return after minimizing");
  const closing = await openWindow(new URL(window.webContents.getURL()).origin, {
    bounds: { width: 800, height: 600 },
    fatal: (error) => {
      throw error;
    },
  });
  try {
    await page(closing).wait("document.querySelector('[aria-label=Close]')");
    closing.maximize();
    await until(() => closing.isMaximized(), "auxiliary window maximized before Close");
    await click(closing, "document.querySelector('[aria-label=Close]')");
    await until(() => closing.isDestroyed(), "native Close button while maximized");
  } finally {
    if (!closing.isDestroyed()) closing.destroy();
  }

  window.unmaximize();
  await until(() => !window.isMaximized(), "normal state before double-click setup");
  await setView(window, [800, 600], 1);
  window.maximize();
  await until(() => window.isMaximized(), "maximized before native double-click");
  await driver.wait("document.querySelector('[aria-label=Restore]')");
  await driver.js(`(() => {
    window.titleClickTrace = [];
    for (const type of ['pointerup', 'click', 'dblclick', 'gotpointercapture', 'lostpointercapture'])
      document.addEventListener(type, e => window.titleClickTrace.push({type, target: e.target.dataset.cursorId ?? e.target.tagName}), true);
  })()`);
  await click(window, TITLE, "double");
  try {
    await until(() => !window.isMaximized(), "native double-click to restore maximized title");
  } catch (cause) {
    throw new Error(
      `Maximized title events: ${JSON.stringify(await driver.js("window.titleClickTrace"))}`,
      { cause },
    );
  }
  await click(window, TITLE, "double");
  await until(() => window.isMaximized(), "native double-click to maximize normal title");

  // Each independent gesture starts with a known normal size, also after WM-controlled double-clicks.
  window.unmaximize();
  await until(() => !window.isMaximized(), "normal state before restore-drag setup");
  await setView(window, [800, 600], 1);
  window.maximize();
  await until(() => window.isMaximized(), "maximized window before restore-drag");
  await driver.wait("document.querySelector('[aria-label=Restore]')");
  const maximizedBounds = window.getBounds();
  const titleHeight = await driver.js(`${TITLE}.closest("header").getBoundingClientRect().height`);
  const maximized = await point(window, TITLE);
  await input("move", maximized.x, maximized.y);
  try {
    await input("down");
    await frames(driver);
    await input("move", maximized.x + 2, maximized.y + 1);
    await frames(driver);
    assert.ok(window.isMaximized(), "a three-pixel gesture does not restore the window");
    await input("move", maximized.x + 2, maximized.y + 2);
    await until(() => !window.isMaximized(), "dragging the maximized title to restore");
    await input("move", maximized.x - 100, maximized.y + 160);
    await until(() => {
      const bounds = window.getBounds();
      return (
        bounds.x ===
          Math.round(
            maximized.x - 100 - (800 * (maximized.x - maximizedBounds.x)) / maximizedBounds.width,
          ) && bounds.y === Math.round(maximized.y + 160 - titleHeight / 2)
      );
    }, "continuing the same restore gesture to its pointer-relative position");
  } catch (error) {
    error.message += `; bounds=${JSON.stringify(window.getBounds())}; normal=${JSON.stringify(window.getNormalBounds())}; events=${JSON.stringify(await driver.js("window.titleClickTrace"))}`;
    throw error;
  } finally {
    await input("up");
  }
  assert.deepEqual(window.getSize(), [800, 600], "restore drag recovers the normal size");
  const restored = window.getBounds();
  await input("move", 1200, 950);
  await frames(driver);
  assert.deepEqual(window.getBounds(), restored, "release ends the restore drag");
  assert.equal(
    restored.x,
    Math.round(
      maximized.x - 100 - (800 * (maximized.x - maximizedBounds.x)) / maximizedBounds.width,
    ),
    "restore keeps the horizontal pointer fraction",
  );
  assert.equal(
    restored.y,
    Math.round(maximized.y + 160 - titleHeight / 2),
    "restore puts the pointer halfway down the measured title bar",
  );
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
    const before = window.getBounds();
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
    if (horizontal < 0) {
      assert.ok(bounds.x < before.x, `${name} moves left`);
      assert.equal(bounds.x + bounds.width, before.x + before.width, `${name} anchors right`);
    }
    if (horizontal > 0) {
      assert.ok(bounds.x + bounds.width > before.x + before.width, `${name} moves right`);
      assert.equal(bounds.x, before.x, `${name} anchors left`);
    }
    if (vertical < 0) {
      assert.ok(bounds.y < before.y, `${name} moves up`);
      assert.equal(bounds.y + bounds.height, before.y + before.height, `${name} anchors bottom`);
    }
    if (vertical > 0) {
      assert.ok(bounds.y + bounds.height > before.y + before.height, `${name} moves down`);
      assert.equal(bounds.y, before.y, `${name} anchors top`);
    }
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
