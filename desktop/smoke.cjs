// Desktop smoke test and screenshot harness, run by `make tests-desktop` on a private display.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");

const SCREENSHOTS = path.resolve(__dirname, "../artifacts/screenshots");
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

module.exports = async (window) => {
  const js = (expression) => window.webContents.executeJavaScript(expression);
  const wait = async (expression) => {
    for (let i = 0; i < 100; i++) {
      if (await js(expression)) return;
      await delay(100);
    }
    throw new Error(`Timed out: ${expression}\n${await js("document.body.innerText")}`);
  };
  await wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  assert.equal(await js("document.querySelector('[role=alert]')"), null);

  await fs.mkdir(SCREENSHOTS, { recursive: true });
  const backgrounds = {};
  for (const theme of ["light", "dark"]) {
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    backgrounds[theme] = await js("getComputedStyle(document.body).backgroundColor");
    // Capturing before the compositor has produced a frame for the new theme fails.
    await js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");
    const image = await window.webContents.capturePage();
    await fs.writeFile(path.join(SCREENSHOTS, `main-${theme}.png`), image.toPNG());
  }
  assert.notEqual(backgrounds.light, backgrounds.dark, "The window must follow the theme");

  // The title bar has the window controls, and Ctrl+= and Ctrl+0 zoom the page.
  for (const name of ["Minimize", "Maximize", "Close"])
    assert.notEqual(await js(`document.querySelector('[aria-label=${name}]')`), null, name);
  const press = (keyCode) =>
    window.webContents.sendInputEvent({ type: "keyDown", keyCode, modifiers: ["control"] });
  press("=");
  await wait("window.devicePixelRatio > 1");
  press("0");
  await wait("window.devicePixelRatio === 1");

  // The smallest window, at normal zoom and at 200%: the View menu, then the Settings dialog, in both themes.
  window.setSize(640, 480);
  const frame = () =>
    js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");
  const shoot = async (name) => {
    await frame();
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };
  for (const [theme, zoom] of [
    ["light", 1],
    ["dark", 1],
    ["light", 2],
    ["dark", 2],
  ]) {
    window.webContents.setZoomFactor(zoom);
    const suffix = zoom === 1 ? "" : "-200";
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    await js(
      "!document.querySelector('[role=menu]') && [...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'View').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await shoot(`menu-${theme}${suffix}.png`);
    await js("document.querySelector('[aria-label=Settings]').click()");
    await wait("document.querySelector('dialog[open]')");
    await shoot(`settings-${theme}${suffix}.png`);
    await js(
      "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Cancel').click()",
    );
    await wait("!document.querySelector('dialog')");
  }
  console.log(
    `PASS: app shell loaded; screenshots in ${path.relative(path.resolve(__dirname, ".."), SCREENSHOTS)}`,
  );
};
