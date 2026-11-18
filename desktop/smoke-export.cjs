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
  await wait("document.querySelector('dialog[open] h2')?.textContent === 'Export Video'");
  // The video's one lane has no box to check; once its catalog and data are in, the dialog can start.
  const submit = "document.querySelector('dialog[open] button[type=submit]')";
  assert.equal(await js("document.querySelector('dialog[open] [role=group]')"), null);
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
  // The fixed part of the dialog (every field and both buttons) shows whole without scrolling anything; only a lane
  // list, which grows with the video's lanes and which this video has none of, may scroll.
  const unscrolled = () => {
    const problems = [];
    const box = document.querySelector("dialog[open]").getBoundingClientRect();
    for (const element of document.querySelectorAll("dialog[open] *")) {
      if (
        /^(auto|scroll)$/.test(getComputedStyle(element).overflowY) &&
        element.scrollHeight > element.clientHeight + 1
      )
        problems.push(
          `scrolls by ${element.scrollHeight - element.clientHeight}px: ${element.tagName.toLowerCase()}`,
        );
    }
    for (const control of document.querySelectorAll(
      "dialog[open] select, dialog[open] input, dialog[open] button",
    )) {
      const rect = control.getBoundingClientRect();
      if (rect.top < box.top - 1 || rect.bottom > box.bottom + 1)
        problems.push(
          `outside the dialog: ${control.ariaLabel || control.textContent || control.tagName}`,
        );
    }
    return problems;
  };
  for (const [shown, zoom, size] of [
    ["light", 1, "system"],
    ["dark", 1, "system"],
    ["light", 1, "larger"],
    ["dark", 1, "larger"],
    ["light", 2, "system"],
    ["dark", 2, "system"],
  ]) {
    await setView(window, [640, 480], zoom);
    nativeTheme.themeSource = shown;
    await js(`document.documentElement.dataset.textSize = ${JSON.stringify(size)}`);
    await wait(`matchMedia('(prefers-color-scheme: ${shown})').matches`);
    await wait("document.querySelector('dialog[open]').getBoundingClientRect().height > 0");
    await frames({ js });
    const where = `640x480 at ${zoom * 100}% zoom, ${size} text, ${shown}`;
    assert.deepEqual(
      await js(`(${fit.dialog.toString()})()`),
      [],
      `The export dialog must fit the ${where}`,
    );
    assert.deepEqual(
      await js(`(${unscrolled.toString()})()`),
      [],
      `The export dialog's fields and buttons must show without scrolling at ${where}`,
    );
    await fs.writeFile(
      path.join(
        SCREENSHOTS,
        `export-${shown}${zoom === 1 ? "" : "-200"}${size === "larger" ? "-larger" : ""}.png`,
      ),
      (await window.webContents.capturePage()).toPNG(),
    );
  }
  await js('document.documentElement.dataset.textSize = "system"');
  nativeTheme.themeSource = theme;
  await setView(window, [1100, 720], 1);
  // Before an export starts Cancel is there, and closes the dialog like Escape.
  await js(
    "[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'Cancel').click()",
  );
  await wait("!document.querySelector('dialog')");
};
