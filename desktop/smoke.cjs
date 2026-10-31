// Desktop smoke test and screenshot harness, run by `make tests-desktop` on a private display.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const fit = require("./fit.cjs");
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
    // Nothing is open: the page must fit the window, and every control must be reachable.
    await js(
      "document.querySelector('[role=menu]') && document.querySelector('[aria-expanded=true]').click()",
    );
    await frame();
    // With the sidebar open, then closed (the welcome area shows).
    for (const sidebar of ["open", "closed"]) {
      assert.deepEqual(
        await js(`(${fit.toString()})()`),
        [],
        `The page must fit the 640x480 window at ${zoom * 100}% zoom, sidebar ${sidebar}`,
      );
      if (zoom === 2) await shoot(`layout-${sidebar}-${theme}${suffix}.png`);
      await js("document.querySelector('[aria-label=Workspace]').click()");
      await frame();
    }
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

  // A real video through the real backend: the canvas shows a picture, and stepping changes it.
  const video = path.resolve(__dirname, "../fixtures/video/long_gop_h264.mp4");
  window.setSize(1100, 720);
  window.webContents.setZoomFactor(1);
  assert.equal(
    await js(`fetch("/api/workspace/items", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ items: [{ type: "Video", path: ${JSON.stringify(video)} }], open: true }),
    }).then((response) => response.ok)`),
    true,
  );
  const shownFrame = `Number.parseInt(document.querySelector("output[aria-label=Frame]")?.textContent ?? "")`;
  await wait(`${shownFrame} === 0`);
  // Frame N of this video is flat with luma 7 N, so frame 0 is black and frame 12 a visible grey.
  const centre = async () => {
    await frame();
    const image = await window.webContents.capturePage();
    const [x, y, w, h] = await js(
      "(() => { const r = document.querySelector('canvas').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()",
    );
    const scale = image.getSize().width / (await js("innerWidth"));
    const bitmap = image.toBitmap();
    const at =
      (Math.round((y + h / 2) * scale) * image.getSize().width + Math.round((x + w / 2) * scale)) *
      4;
    return Math.max(bitmap[at], bitmap[at + 1], bitmap[at + 2]);
  };
  await js("document.querySelector('[aria-label=Pause]')?.click()");
  const black = await centre();
  for (let step = 1; step <= 12; step++) {
    await js("document.querySelector('[aria-label=\"Forward 1 frame\"]').click()");
    await wait(`${shownFrame} === ${step}`);
  }
  const grey = await centre();
  assert.ok(black < 16, `Frame 0 of the video is black, got ${black}`);
  assert.ok(grey > 40, `Frame 12 must be a visible grey, got ${grey}: the canvas is still black`);
  console.log(
    `PASS: app shell loaded; screenshots in ${path.relative(path.resolve(__dirname, ".."), SCREENSHOTS)}`,
  );
};
