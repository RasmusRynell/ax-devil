// The smoke test's export scenario on the video that is open: Ctrl+E opens the export dialog with the video's lane
// ready, Export asks the native save dialog (answered by a stub here) and exports nothing when it is cancelled, and the
// dialog fits the smallest window at 200% text size, in both themes.
const { pressShortcut } = require("./shortcut-input.cjs");
const { dialog, nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
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
  // The fixed part of the dialog (every field and both buttons) shows whole without scrolling anything, with and
  // without a refusal; only a lane list, which this video has none of, may scroll.
  const everywhere = async (state, suffix) => {
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
      const where = `640x480 at ${zoom * 100}% zoom, ${size} text, ${shown}, ${state}`;
      assert.deepEqual(
        await js(`(${fit.dialog.toString()})()`),
        [],
        `The export dialog must fit the ${where}`,
      );
      assert.deepEqual(
        await js(`(${fit.fixed.toString()})()`),
        [],
        `The export dialog's fields and buttons must show without scrolling at ${where}`,
      );
      await fs.writeFile(
        path.join(
          SCREENSHOTS,
          `export${suffix}-${shown}${zoom === 1 ? "" : "-200"}${size === "larger" ? "-larger" : ""}.png`,
        ),
        (await window.webContents.capturePage()).toPNG(),
      );
    }
    await js('document.documentElement.dataset.textSize = "system"');
  };
  await everywhere("idle", "");
  // A frame that is no number: the row says so on the label's line.
  const fromBox = "document.querySelector('dialog[open] input[aria-label=\"From frame\"]')";
  await js(`(() => {
    const input = ${fromBox};
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'abc');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await wait(`${fromBox}.getAttribute('aria-invalid') === 'true'`);
  await everywhere("frame error", "-error");
  await js(`(() => {
    const input = ${fromBox};
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '0');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`);
  await wait(`!${submit}.disabled`);
  nativeTheme.themeSource = theme;
  await setView(window, [1100, 720], 1);
  // Before an export starts Cancel is there, and closes the dialog like Escape.
  await js(
    "[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'Cancel').click()",
  );
  await wait("!document.querySelector('dialog')");

  // A finished export: the bar and where the file went, with one Close button, fit the same way.
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "ax-export-"));
  const saving = dialog.showSaveDialog.bind(dialog);
  dialog.showSaveDialog = async () => ({ canceled: false, filePath: path.join(folder, "out.mp4") });
  try {
    pressShortcut(window, "Ctrl+E");
    await wait(`${submit} && !${submit}.disabled`);
    await js(`${submit}.click()`);
    await wait(`${submit}?.textContent === 'Close'`);
    await everywhere("finished", "-done");
    await js(`${submit}.click()`);
    await wait("!document.querySelector('dialog')");
    nativeTheme.themeSource = theme;
    await setView(window, [1100, 720], 1);
  } finally {
    dialog.showSaveDialog = saving;
    await fs.rm(folder, { recursive: true, force: true });
  }
};
