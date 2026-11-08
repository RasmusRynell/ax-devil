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
const { events } = require("./smoke-events.cjs");
const { fields } = require("./smoke-fields.cjs");
const { twice } = require("./smoke-twice.cjs");
const kit = require("./smoke-kit.cjs");
const { pauseAtStart } = require("./smoke-video.cjs");
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
    await fill("MOT Root", DATASET);
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
  // The video's pane makes room: these scenarios read the one viewer on screen.
  const panes = "[...document.querySelectorAll('[role=group][data-cursor-id^=\"pane:\"]')]";
  await wait(`${panes}.length === 2`);
  await js(
    `${panes}.find((pane) => pane.ariaLabel.endsWith('.mp4')).querySelector('[aria-label="Close this pane"]').click()`,
  );
  await wait(`${panes}.length === 1`);

  const laneCells = () => kit.laneCells(window);
  const goToFrame = (target) => kit.goToFrame(window, target);
  const showEntry = async (label, lanes) => {
    await wait(`document.querySelector('[aria-label="Entry: ${label}"]')`);
    await wait(
      `document.querySelectorAll('canvas')[0]?.parentElement.parentElement.querySelectorAll(':scope > div.absolute:not(.inset-0)').length === ${lanes}`,
    );
    await wait(`${SHOWN} >= 0`);
    await pauseAtStart(window);
  };

  // Every lane has boxes on frame 0; frame 5 has no rows in any file, which the files leave open, so sticky keeps
  // frame 4's boxes on it.
  const expectOverlays = async (lanes) => {
    for (const [at, drawn] of [
      [0, true],
      [5, true],
      [0, true],
    ]) {
      await goToFrame(at);
      const cells = await laneCells();
      assert.deepEqual(
        cells.map((cell) => cell.label),
        lanes,
      );
      // Marks are drawn once the lane's sample is held, and again when its lane is resized: wait until they are on
      // screen. One capture serves every lane.
      const drawnCells = async () => {
        const shot = await kit.capture(window);
        return cells.map((cell) => {
          for (
            let y = Math.round(cell.y * shot.scale);
            y < Math.round(cell.bottom * shot.scale);
            y++
          )
            for (
              let x = Math.round(cell.x * shot.scale);
              x < Math.round(cell.right * shot.scale);
              x++
            )
              if (kit.colourful(shot, x, y)) return true;
          return false;
        });
      };
      const deadline = Date.now() + 5000;
      let seen = await drawnCells();
      while (!seen.every((found) => found === drawn) && Date.now() < deadline) {
        await delay(100);
        seen = await drawnCells();
      }
      cells.forEach((cell, i) =>
        assert.equal(
          seen[i],
          drawn,
          `${cell.label}, frame ${at}: boxes ${drawn ? "missing" : "left over"}`,
        ),
      );
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
  // The transport takes two seconds to fade each time it has been shown, and the picture is captured without it: draw
  // and shoot every view first, then show it and check the fit in every view.
  for (const [theme, zoom] of THEMES_AND_ZOOMS) {
    await setView(window, [640, 480], zoom);
    await setTheme(theme);
    await expectOverlays(entries[1].lanes);
    await shoot(`playlist-min-${theme}${zoom === 1 ? "" : "-200"}.png`);
  }
  for (const [theme, zoom] of THEMES_AND_ZOOMS) {
    await setView(window, [640, 480], zoom);
    await setTheme(theme);
    await showChrome(window);
    assert.deepEqual(
      await js(`(${fit.page.toString()})()`),
      [],
      `The playlist viewer must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
    // The transport and entry controls wrap onto further lines when the width runs out, and none leaves the window.
    const { missing, outside, lines } = await js(`(() => {
      const named = ['[aria-label=Play]', '[aria-label="Back 1 frame"]', '[aria-label="Forward 1 frame"]',
        '[aria-label="Previous entry"]', '[aria-label="Next entry"]', '[aria-label=Time]'];
      const rects = named.map((selector) => [selector, document.querySelector(selector)?.getBoundingClientRect()]);
      const tops = new Set(rects.filter(([, rect]) => rect).map(([, rect]) => Math.round(rect.top)));
      return {
        missing: rects.filter(([, rect]) => !rect?.width).map(([selector]) => selector),
        outside: rects.filter(([, rect]) => rect && (rect.left < 0 || rect.right > innerWidth)).map(([selector]) => selector),
        lines: tops.size,
      };
    })()`);
    assert.deepEqual(
      [missing, outside],
      [[], []],
      "Every transport and entry control is reachable",
    );
    if (zoom === 2)
      assert.ok(
        lines >= 2,
        `The entry controls and the transport must be on lines of their own: ${lines}`,
      );
  }
  await setView(window, [1100, 720], 1);
  await js("document.querySelector('[aria-label=Workspace]').click()");

  // The pointer, theme and zoom scenarios run on MINI-02's one lane, on frame 0.
  await js("document.querySelector('[aria-label=\"Previous entry\"]').click()");
  await showEntry("MINI-02", 1);
  await goToFrame(0);
  const { point } = await overlays(window, { setTheme });
  await panels(window, { setTheme, shoot, showEntry, point });
  await objects(window, { setTheme, shoot });

  // The playlist's pane closes and the video opens again, as the one open item for the scenarios that follow.
  await js(`${panes}[0].querySelector('[aria-label="Close this pane"]').click()`);
  await wait(`${panes}.length === 0`);
  const videoRow =
    "[...document.querySelectorAll('[role=treeitem]')].find((row) => row.textContent.startsWith('long_gop_h264.mp4'))";
  await wait(videoRow);
  await js(`${videoRow}.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))`);
  await wait(`${panes}.map((pane) => pane.ariaLabel).join() === 'long_gop_h264.mp4'`);

  // A resolver that returns two playlists: the dialog's states and the tree they make.
  await multiplaylist(window);

  // One overlay file given twice is two lanes, in a window of its own on another backend.
  await twice();
  // Two ADF files on a video: the object page of an object with colours, a motion state and a picture.
  await fields();
  // An ADF file with events on a video: the Events list, an event's page and its objects' pages.
  await events();
};
