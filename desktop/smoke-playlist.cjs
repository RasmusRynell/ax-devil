// The smoke test's playlist scenario: a MOT Challenge dataset added through the Add Playlist dialog, whose lanes draw
// their files' boxes on the picture, checked on the real canvas in both themes, the smallest window and 200% zoom.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const { objects } = require("./smoke-objects.cjs");
const { multiplaylist } = require("./smoke-multiplaylist.cjs");
const { overlays } = require("./smoke-overlays.cjs");
const { panels } = require("./smoke-panels.cjs");
const { twice } = require("./smoke-twice.cjs");
const kit = require("./smoke-kit.cjs");
const { SCREENSHOTS, SHOWN, SMOKE_ROOT, delay, frames, page, setView, showChrome } = kit;

const DATASET = path.join(SMOKE_ROOT, "fixtures/datasets/mot");
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

  const choose = (select, text) =>
    js(`(() => {
      const select = ${select};
      const option = [...select.options].find((o) => o.textContent === '${text}');
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, option.value);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);
  const labelled = (label) =>
    `[...document.querySelectorAll('dialog[open] label')].find((l) => l.textContent.startsWith('${label}'))`;
  const fill = async (label, text) => {
    await wait(`!!${labelled(label)}`);
    await js(`${labelled(label)}.control.focus()`);
    window.webContents.insertText(text);
    await wait(`${labelled(label)}.control.value === ${JSON.stringify(text)}`);
  };

  // File > Add Playlist and a resolver. MOT Challenge: the dataset folder typed in and scanned. Folder Pair: both
  // folders typed in and MOT Annotations chosen, which brings in that decoder's options.
  const openAddPlaylist = async (resolver = "MOT Challenge") => {
    await closeMenu();
    await js(
      "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'File').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await js(
      "[...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent.startsWith('Add Playlist')).click()",
    );
    await wait("!!document.querySelector('dialog[open] select')");
    await choose("document.querySelector('dialog[open] select')", resolver);
    if (resolver === "Folder Pair") {
      await fill("Videos", DATASET);
      await fill("Overlays", DATASET);
      await choose(`${labelled("Decoder")}.control`, "MOT Annotations (MOT_FILE)");
      await wait(
        "/Image width/.test(document.querySelector('dialog[open]').innerText) && /Image height/.test(document.querySelector('dialog[open]').innerText)",
      );
      // At 200% the options are below the fold of the fields' own scroll: show them.
      await js(
        "document.querySelector('dialog[open] select').closest('.overflow-y-auto').scrollTo(0, 1e6)",
      );
      return;
    }
    await fill("Dataset folder", DATASET);
    await clickButton("Scan");
    await wait("/MINI-01/.test(document.querySelector('dialog[open]').innerText)");
    await wait("/MINI-02/.test(document.querySelector('dialog[open]').innerText)");
  };

  // Each form at the smallest window, then Add at the normal size.
  for (const [resolver, name] of [
    ["MOT Challenge", "add-playlist"],
    ["Folder Pair", "add-playlist-pair"],
  ]) {
    for (const [theme, zoom] of THEMES_AND_ZOOMS) {
      await setView(window, [640, 480], zoom);
      await setTheme(theme);
      await openAddPlaylist(resolver);
      await shoot(`${name}-${theme}${zoom === 1 ? "" : "-200"}.png`);
      // A folder box scrolls its own long path, which the check reports as text cut off with no name.
      assert.deepEqual(
        (await js(`(${fit.dialog.toString()})()`)).filter((problem) => problem !== 'cut off: ""'),
        [],
        `The Add Playlist dialog (${resolver}) must fit the 640x480 window at ${zoom * 100}% zoom`,
      );
      await clickButton("Cancel");
      await wait("!document.querySelector('dialog')");
    }
  }
  window.webContents.setZoomFactor(1);
  await setTheme("light");
  window.setSize(1100, 720);
  await openAddPlaylist();
  await clickButton("Load Playlist");
  await wait("!document.querySelector('dialog')");

  const laneCells = () => kit.laneCells(window);
  const colourful = (cell) => kit.colourCount(window, cell);
  const goToFrame = (target) => kit.goToFrame(window, target);
  const showEntry = async (label, lanes) => {
    await wait(`document.querySelector('[aria-label="Entry: ${label}"]')`);
    await wait(
      `document.querySelectorAll('canvas')[0]?.parentElement.parentElement.querySelectorAll(':scope > div.absolute:not(.inset-0)').length === ${lanes}`,
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
    await showChrome(window);
    assert.deepEqual(
      await js(`(${fit.page.toString()})()`),
      [],
      `The playlist viewer must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
  }
  window.webContents.setZoomFactor(1);
  window.setSize(1100, 720);
  await js("document.querySelector('[aria-label=Workspace]').click()");

  // The pointer, theme and zoom scenarios run on MINI-02's one lane, on frame 0.
  await js("document.querySelector('[aria-label=\"Previous entry\"]').click()");
  await showEntry("MINI-02", 1);
  await goToFrame(0);
  const { point } = await overlays(window, { setTheme });
  await panels(window, { setTheme, shoot, showEntry, point });
  await objects(window, { setTheme, shoot });

  // The playlist's tab closes, which leaves the video as the one open item for the scenarios that follow.
  await js(
    "[...document.querySelectorAll('[aria-label^=\"Close \"]')].find((b) => !b.ariaLabel.endsWith('.mp4')).click()",
  );
  await wait(
    "[...document.querySelectorAll('[role=tab]')].map((tab) => tab.textContent).join() === 'long_gop_h264.mp4'",
  );

  // A resolver that returns two playlists: the dialog's states and the tree they make.
  await multiplaylist(window);

  // One overlay file given twice is two lanes, in a window of its own on another backend.
  await twice();
};
