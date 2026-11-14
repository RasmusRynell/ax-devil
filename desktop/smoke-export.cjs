// The smoke test's export scenario on the video that is open: Ctrl+E opens the export dialog with the video's lane
// ready, Export asks the native save dialog (answered by a stub here) and exports nothing when it is cancelled, and the
// dialog fits the smallest window at 200% text size, in both themes.
const { pressShortcut } = require("./shortcut-input.cjs");
const { dialog, nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const { SCREENSHOTS, frames, page, setView, until } = require("./smoke-kit.cjs");

module.exports.run = async ({ window }) => {
  const { js, wait } = page(window);
  const theme = nativeTheme.themeSource;
  await setView(window, [1100, 720], 1);
  await js("document.querySelector('[aria-label=Pause]')?.click()");
  // The shell's key handler hears a real key only while the window has the keyboard.
  await wait("document.hasFocus()");
  await frames({ js });
  pressShortcut(window, "Ctrl+E");
  await wait(
    "document.querySelector('dialog[open] h2')?.textContent === 'Export Video with Overlays'",
  );
  // The video's one lane has no box to check; once its catalog and data are in, the dialog can start.
  const submit = "document.querySelector('dialog[open] button[type=submit]')";
  assert.equal(await js("document.querySelector('dialog[open] fieldset')"), null);
  await wait(`!${submit}.disabled`);
  assert.equal(
    await js(`${submit}.disabled`),
    false,
    "Export must be possible with the lane ready",
  );

  // Export asks the native save dialog for an .mp4 named after the video; cancelling it exports nothing.
  const original = dialog.showSaveDialog.bind(dialog);
  const asked = [];
  dialog.showSaveDialog = async (_window, options) => {
    asked.push(options);
    return { canceled: true };
  };
  try {
    await js(`${submit}.click()`);
    await until(() => asked.length > 0, "the native save dialog to be asked");
  } finally {
    dialog.showSaveDialog = original;
  }
  assert.equal(asked.length, 1, "Export must ask the native save dialog once");
  assert.equal(asked[0].defaultPath, "long_gop_h264.mp4");
  assert.deepEqual(asked[0].filters, [{ name: "MP4 Video", extensions: ["mp4"] }]);
  await wait(`!${submit}.disabled`);
  assert.equal(await js("document.querySelector('[role=progressbar]')"), null);

  await fs.mkdir(SCREENSHOTS, { recursive: true });
  for (const [shown, zoom] of [
    ["light", 1],
    ["dark", 1],
    ["light", 2],
    ["dark", 2],
  ]) {
    await setView(window, [640, 480], zoom);
    nativeTheme.themeSource = shown;
    await wait(`matchMedia('(prefers-color-scheme: ${shown})').matches`);
    await wait("document.querySelector('dialog[open]').getBoundingClientRect().height > 0");
    await frames({ js });
    assert.deepEqual(
      await js(`(${fit.dialog.toString()})()`),
      [],
      `The export dialog must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
    await fs.writeFile(
      path.join(SCREENSHOTS, `export-${shown}${zoom === 1 ? "" : "-200"}.png`),
      (await window.webContents.capturePage()).toPNG(),
    );
  }
  nativeTheme.themeSource = theme;
  await setView(window, [1100, 720], 1);
  // Before an export starts there is nothing to cancel, so Cancel is disabled; Escape still closes the dialog.
  assert.equal(
    await js(
      "[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'Cancel').disabled",
    ),
    true,
    "Cancel must be disabled before an export starts",
  );
  pressShortcut(window, "Escape");
  await wait("!document.querySelector('dialog')");
};
