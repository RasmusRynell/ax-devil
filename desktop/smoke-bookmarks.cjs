// The smoke test's bookmark scenario on the video that is open: M marks the frame on screen and brings the Bookmarks
// view up, a click on the bookmark returns to its frame, Export CSV writes the file the native save dialog (answered by
// a stub here) chose, and the view fits the smallest window at 200% text size, in both themes.
const { pressShortcut } = require("./shortcut-input.cjs");
const { dialog, nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const {
  SCREENSHOTS,
  SHOWN,
  frames,
  goToFrame,
  page,
  setView,
  showChrome,
  until,
} = require("./smoke-kit.cjs");

module.exports.run = async ({ window }) => {
  const { js, wait } = page(window);
  const theme = nativeTheme.themeSource;
  await setView(window, [1100, 720], 1);
  await goToFrame(window, 12);
  // The shortcut belongs to the viewer: the page has the keyboard once the picture is focused.
  await js("document.querySelector('main [tabindex]')?.focus()");
  pressShortcut(window, "M");
  const BOOKMARKS = "aside[aria-label=Sidebar] section[aria-label=Bookmarks]";
  await wait(`document.querySelector('${BOOKMARKS} button')`);
  const rows = () =>
    js(
      `[...document.querySelectorAll('${BOOKMARKS} button[data-cursor-id^="bookmark:"]')].map((b) => b.textContent)`,
    );
  const [row] = await rows();
  assert.match(row, /^Frame 12.*frame 12 · Note$/, "M must mark the frame on screen");
  assert.equal(await js(SHOWN), 12, "Marking must leave the viewer where it was");

  // A click on the bookmark shows its frame again, paused.
  await goToFrame(window, 3);
  await js(`document.querySelector('${BOOKMARKS} button[data-cursor-id^="bookmark:"]').click()`);
  await wait(`${SHOWN} === 12`);
  // The bookmark opened last is marked; the screenshots below show it.
  await wait(`document.querySelector('${BOOKMARKS} button[aria-current="true"]')`);

  // Export CSV asks the native save dialog for a .csv and writes what it answers.
  const target = path.join(SCREENSHOTS, "..", "bookmarks.csv");
  await fs.rm(target, { force: true });
  const original = dialog.showSaveDialog.bind(dialog);
  const asked = [];
  dialog.showSaveDialog = async (_window, options) => {
    asked.push(options);
    return { canceled: false, filePath: target };
  };
  try {
    await js("document.querySelector('[aria-label=\"Export CSV\"]').click()");
    await until(() => asked.length > 0, "the native save dialog to be asked");
    await until(
      () =>
        fs.stat(target).then(
          () => true,
          () => false,
        ),
      "the CSV to be written",
    );
  } finally {
    dialog.showSaveDialog = original;
  }
  assert.equal(
    asked[0].defaultPath,
    path.join(require("electron").app.getPath("home"), "Untitled bookmarks.csv"),
  );
  assert.deepEqual(asked[0].filters, [{ name: "CSV", extensions: ["csv"] }]);
  const lines = (await fs.readFile(target, "utf8")).trim().split("\r\n");
  assert.equal(lines[0], "target,entry,frame,time,kind,note");
  assert.match(lines[1], /^long_gop_h264\.mp4,,12,\d+\.\d{3},Note,$/);

  await fs.mkdir(SCREENSHOTS, { recursive: true });
  for (const [shown, zoom] of [
    ["light", 1],
    ["dark", 1],
    ["light", 2],
    ["dark", 2],
  ]) {
    await setView(window, [zoom === 1 ? 1100 : 640, zoom === 1 ? 720 : 480], zoom);
    nativeTheme.themeSource = shown;
    await wait(`matchMedia('(prefers-color-scheme: ${shown})').matches`);
    await frames({ js });
    // The transport fades when the pointer rests and then takes no pointer, which the fit check would call covered.
    await showChrome(window);
    assert.deepEqual(
      await js(`(${fit.page.toString()})()`),
      [],
      `The Bookmarks view must fit the window at ${zoom * 100}% zoom`,
    );
    await fs.writeFile(
      path.join(SCREENSHOTS, `bookmarks-${shown}${zoom === 1 ? "" : "-200"}.png`),
      (await window.webContents.capturePage()).toPNG(),
    );
  }
  nativeTheme.themeSource = theme;
  await setView(window, [1100, 720], 1);
  // The scenarios after this one start from the Workspace view.
  await js("document.querySelector('[aria-label=Workspace]').click()");
  await wait(
    "document.querySelector('aside[aria-label=Sidebar] [aria-label=\"Workspace items\"]')",
  );
};
