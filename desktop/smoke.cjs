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
  console.log(
    `PASS: app shell loaded; screenshots in ${path.relative(path.resolve(__dirname, ".."), SCREENSHOTS)}`,
  );
};
