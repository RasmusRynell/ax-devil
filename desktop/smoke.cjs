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
  const frame = () =>
    js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");
  const shoot = async (name) => {
    await frame();
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };

  // The isolated home is a first run, so Quick Setup is open: check it in the smallest window, then Done closes it.
  const size = window.getSize();
  window.setSize(640, 480);
  await wait("document.querySelector('dialog[open] h2')?.textContent === 'Quick Setup'");
  for (const [theme, zoom] of [
    ["light", 1],
    ["dark", 1],
    ["light", 2],
    ["dark", 2],
  ]) {
    window.webContents.setZoomFactor(zoom);
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    await frame();
    assert.deepEqual(
      await js(`(${fit.dialog.toString()})()`),
      [],
      `Quick Setup must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
    await shoot(`quick-setup-${theme}${zoom === 1 ? "" : "-200"}.png`);
  }
  window.webContents.setZoomFactor(1);
  await js(
    "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Done').click()",
  );
  await wait("!document.querySelector('dialog')");
  window.setSize(...size);

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

  // The smallest window, at normal zoom and at 200%: the View menu, then the Settings and Keyboard Shortcuts dialogs, in both themes.
  window.setSize(640, 480);
  const openShortcuts = async () => {
    await js(
      "document.querySelector('[role=menu]') && document.querySelector('[aria-expanded=true]').click()",
    );
    await js(
      "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'File').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await js(
      "[...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent.startsWith('Keyboard Shortcuts')).click()",
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
        await js(`(${fit.page.toString()})()`),
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
    for (const { name, open } of [
      {
        name: "settings",
        open: () => js("document.querySelector('[aria-label=Settings]').click()"),
      },
      {
        name: "settings-storage",
        open: async () => {
          await js("document.querySelector('[aria-label=Settings]').click()");
          await wait("document.querySelector('dialog[open]')");
          await js(
            "[...document.querySelectorAll('[role=tab]')].find((b) => b.textContent === 'Storage').click()",
          );
        },
      },
      { name: "shortcuts", open: openShortcuts },
    ]) {
      await js(
        "document.querySelector('[role=menu]') && document.querySelector('[aria-expanded=true]').click()",
      );
      await open();
      await wait("document.querySelector('dialog[open]')");
      assert.deepEqual(
        await js(`(${fit.dialog.toString()})()`),
        [],
        `The ${name} dialog must fit the 640x480 window at ${zoom * 100}% zoom`,
      );
      await shoot(`${name}-${theme}${suffix}.png`);
      await js(
        "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Cancel').click()",
      );
      await wait("!document.querySelector('dialog')");
    }
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

  // A MOT Challenge playlist through the Add Playlist dialog: its lanes draw their files' boxes on the picture.
  const dataset = path.resolve(__dirname, "../fixtures/datasets/mot");
  const closeMenu = () =>
    js(
      "document.querySelector('[role=menu]') && document.querySelector('[aria-expanded=true]').click()",
    );
  const openAddPlaylist = async () => {
    await closeMenu();
    await js(
      "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'File').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await js(
      "[...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent.startsWith('Add Playlist')).click()",
    );
    await wait("!!document.querySelector('dialog[open] select')");
    await js(`(() => {
      const select = document.querySelector('dialog[open] select');
      const option = [...select.options].find((o) => o.textContent === 'MOT Challenge');
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, option.value);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);
    await wait(
      "[...document.querySelectorAll('dialog[open] label')].some((l) => l.textContent === 'Dataset folder')",
    );
    await js(
      "[...document.querySelectorAll('dialog[open] label')].find((l) => l.textContent === 'Dataset folder').control.focus()",
    );
    window.webContents.insertText(dataset);
    await wait(
      "[...document.querySelectorAll('dialog[open] input')].some((i) => i.value.endsWith('datasets/mot'))",
    );
    await js(
      "[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'Scan').click()",
    );
    await wait("/MINI-01/.test(document.querySelector('dialog[open]').innerText)");
    await wait("/MINI-02/.test(document.querySelector('dialog[open]').innerText)");
  };
  window.setSize(640, 480);
  for (const [theme, zoom] of [
    ["light", 1],
    ["dark", 1],
    ["light", 2],
    ["dark", 2],
  ]) {
    window.webContents.setZoomFactor(zoom);
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    await openAddPlaylist();
    await shoot(`add-playlist-${theme}${zoom === 1 ? "" : "-200"}.png`);
    // The folder box scrolls its own long path, which the check reports as text cut off with no name.
    assert.deepEqual(
      (await js(`(${fit.dialog.toString()})()`)).filter((problem) => problem !== 'cut off: ""'),
      [],
      `The Add Playlist dialog must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
    await js(
      "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Cancel').click()",
    );
    await wait("!document.querySelector('dialog')");
  }
  window.webContents.setZoomFactor(1);
  nativeTheme.themeSource = "light";
  window.setSize(1100, 720);
  await openAddPlaylist();
  await js(
    "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Load').click()",
  );
  await wait("!document.querySelector('dialog')");

  // The lane cells on screen, from the name tags the grid places at each cell's corner.
  const laneCells = () =>
    js(`(() => {
      const grid = document.querySelector('canvas').parentElement;
      const tags = [...grid.querySelectorAll(':scope > div.absolute')].map((tag) => ({
        label: tag.querySelector('span').textContent,
        ...tag.getBoundingClientRect().toJSON(),
      }));
      const end = grid.getBoundingClientRect();
      const next = (values, value, limit) => Math.min(limit, ...values.filter((other) => other > value + 1));
      return tags.map((tag) => ({
        label: tag.label,
        x: tag.left,
        y: tag.top,
        right: next(tags.map((other) => other.left), tag.left, end.right),
        bottom: next(tags.map((other) => other.top), tag.top, end.bottom),
      }));
    })()`);
  // Pixels of a lane cell that carry colour: the grey picture, the white text and the dark name tags do not.
  const colourful = async (cell) => {
    await frame();
    const image = await window.webContents.capturePage();
    const scale = image.getSize().width / (await js("innerWidth"));
    const bitmap = image.toBitmap();
    let count = 0;
    for (let y = Math.round(cell.y * scale); y < Math.round(cell.bottom * scale); y++)
      for (let x = Math.round(cell.x * scale); x < Math.round(cell.right * scale); x++) {
        const at = (y * image.getSize().width + x) * 4;
        const channels = [bitmap[at], bitmap[at + 1], bitmap[at + 2]];
        if (Math.max(...channels) - Math.min(...channels) >= 80) count++;
      }
    return count;
  };
  const goToFrame = async (target) => {
    await js("document.querySelector('[aria-label=Pause]')?.click()");
    for (;;) {
      const at = await js(shownFrame);
      if (at === target) return;
      await js(
        `document.querySelector('[aria-label="${at < target ? "Forward" : "Back"} 1 frame"]').click()`,
      );
      await wait(`${shownFrame} !== ${at}`);
    }
  };
  const showEntry = async (label, lanes) => {
    await wait(`document.querySelector('[aria-label="Entry: ${label}"]')`);
    await wait(`document.querySelectorAll('canvas ~ div.absolute').length === ${lanes}`);
    await wait(`${shownFrame} >= 0`);
  };

  // Every lane has boxes on frame 0; frame 5 has no rows in any file, so nothing is drawn on it.
  const expectOverlays = async (lanes) => {
    for (const [at, drawn] of [
      [0, true],
      [5, false],
      [0, true],
    ]) {
      await goToFrame(at);
      const cells = await laneCells();
      assert.deepEqual(
        cells.map((cell) => cell.label),
        lanes,
      );
      for (const cell of cells) {
        // The overlay arrives with its frame, but a resized lane draws a new one: wait until it is on screen.
        const found = async () => (await colourful(cell)) > 0;
        const deadline = Date.now() + 5000;
        while ((await found()) !== drawn && Date.now() < deadline) await delay(100);
        assert.equal(
          await found(),
          drawn,
          `${cell.label}, frame ${at}: boxes ${drawn ? "missing" : "left over"}`,
        );
      }
    }
  };
  const setTheme = async (theme) => {
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
  };

  // MINI-02 (the test folder lists first) has one lane, DET; MINI-01 has DPM, GT and FRCNN.
  const entries = [
    { entry: "MINI-02", lanes: ["DET"] },
    { entry: "MINI-01", lanes: ["DPM", "GT", "FRCNN"] },
  ];
  for (const { entry, lanes } of entries) {
    if (entry === "MINI-01")
      await js("document.querySelector('[aria-label=\"Next entry\"]').click()");
    await showEntry(entry, lanes.length);
    await expectOverlays(lanes);
    for (const theme of ["light", "dark"]) {
      await setTheme(theme);
      await shoot(`playlist-${entry}-${theme}.png`);
    }
  }

  // The smallest window, at normal zoom and 200%, with the sidebar closed: the lanes still fit and still draw.
  window.setSize(640, 480);
  await js("document.querySelector('[aria-label=Workspace]').click()");
  for (const [theme, zoom] of [
    ["light", 1],
    ["dark", 1],
    ["light", 2],
    ["dark", 2],
  ]) {
    window.webContents.setZoomFactor(zoom);
    await setTheme(theme);
    await expectOverlays(entries[1].lanes);
    await shoot(`playlist-min-${theme}${zoom === 1 ? "" : "-200"}.png`);
    assert.deepEqual(
      await js(`(${fit.page.toString()})()`),
      [],
      `The playlist viewer must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
  }
  console.log(
    `PASS: app shell loaded; screenshots in ${path.relative(path.resolve(__dirname, ".."), SCREENSHOTS)}`,
  );
};
