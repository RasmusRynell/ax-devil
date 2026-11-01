// The smoke test's playlist scenario: a MOT Challenge dataset added through the Add Playlist dialog, whose lanes draw
// their files' boxes on the picture, checked on the real canvas in both themes, the smallest window and 200% zoom.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const { SCREENSHOTS, delay, frames, page, setView } = require("./smoke-kit.cjs");

const DATASET = path.resolve(__dirname, "../fixtures/datasets/mot");
const SHOWN = `Number.parseInt(document.querySelector("output[aria-label=Frame]")?.textContent ?? "")`;
const THEMES_AND_ZOOMS = [
  ["light", 1],
  ["dark", 1],
  ["light", 2],
  ["dark", 2],
];

module.exports.playlist = async (window) => {
  const { js, wait } = page(window);
  const shoot = async (name) => {
    await frames({ js });
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };
  const setTheme = async (theme) => {
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
  };
  const clickButton = (label) =>
    js(
      `[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === '${label}').click()`,
    );
  const closeMenu = () =>
    js(
      "document.querySelector('[role=menu]') && document.querySelector('[aria-expanded=true]').click()",
    );

  // File > Add Playlist, the MOT Challenge resolver, the dataset folder typed in and scanned.
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
    window.webContents.insertText(DATASET);
    await wait(
      "[...document.querySelectorAll('dialog[open] input')].some((i) => i.value.endsWith('datasets/mot'))",
    );
    await clickButton("Scan");
    await wait("/MINI-01/.test(document.querySelector('dialog[open]').innerText)");
    await wait("/MINI-02/.test(document.querySelector('dialog[open]').innerText)");
  };

  // The dialog at the smallest window, then Add at the normal size.
  for (const [theme, zoom] of THEMES_AND_ZOOMS) {
    await setView(window, [640, 480], zoom);
    await setTheme(theme);
    await openAddPlaylist();
    await shoot(`add-playlist-${theme}${zoom === 1 ? "" : "-200"}.png`);
    // The folder box scrolls its own long path, which the check reports as text cut off with no name.
    assert.deepEqual(
      (await js(`(${fit.dialog.toString()})()`)).filter((problem) => problem !== 'cut off: ""'),
      [],
      `The Add Playlist dialog must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
    await clickButton("Cancel");
    await wait("!document.querySelector('dialog')");
  }
  window.webContents.setZoomFactor(1);
  await setTheme("light");
  window.setSize(1100, 720);
  await openAddPlaylist();
  await clickButton("Add");
  await wait("!document.querySelector('dialog')");

  // The lane cells on screen, from the name tags the grid places at each cell's corner (the pointer anchors beside them are hidden from assistive technology).
  const laneCells = () =>
    js(`(() => {
      const grid = document.querySelector('canvas').parentElement;
      const tags = [...grid.querySelectorAll(':scope > div.absolute:not([aria-hidden])')].map((tag) => ({
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
    await frames({ js });
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
      const at = await js(SHOWN);
      if (at === target) return;
      await js(
        `document.querySelector('[aria-label="${at < target ? "Forward" : "Back"} 1 frame"]').click()`,
      );
      await wait(`${SHOWN} !== ${at}`);
    }
  };
  const showEntry = async (label, lanes) => {
    await wait(`document.querySelector('[aria-label="Entry: ${label}"]')`);
    await wait(
      `document.querySelectorAll('canvas ~ div.absolute:not([aria-hidden])').length === ${lanes}`,
    );
    await wait(`${SHOWN} >= 0`);
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
        // Marks are drawn once the lane's sample is held, and again when its lane is resized: wait until they are on screen.
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
  await setView(window, [640, 480], 1);
  await js("document.querySelector('[aria-label=Workspace]').click()");
  for (const [theme, zoom] of THEMES_AND_ZOOMS) {
    await setView(window, [640, 480], zoom);
    await setTheme(theme);
    await expectOverlays(entries[1].lanes);
    await shoot(`playlist-min-${theme}${zoom === 1 ? "" : "-200"}.png`);
    assert.deepEqual(
      await js(`(${fit.page.toString()})()`),
      [],
      `The playlist viewer must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
  }
  window.webContents.setZoomFactor(1);
  window.setSize(1100, 720);
  await js("document.querySelector('[aria-label=Workspace]').click()");

  // The playlist's tab closes, which leaves the video as the one open item for the scenarios that follow.
  await js(
    "[...document.querySelectorAll('[aria-label^=\"Close \"]')].find((b) => !b.ariaLabel.endsWith('.mp4')).click()",
  );
  await wait(
    "[...document.querySelectorAll('[role=tab]')].map((tab) => tab.textContent).join() === 'long_gop_h264.mp4'",
  );
};
