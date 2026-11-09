// The smoke test's item-actions scenario: the row menu offers an item what it can do; Information lists a video; Edit starts from the item, and
// saving a new name renames it; Delete removes it. Screenshots of each state in both themes.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const fit = require("./fit.cjs");
const {
  delay,
  frames,
  openAddVideo,
  page,
  removeTemp,
  SCREENSHOTS,
  setView,
  SMOKE_ROOT,
} = require("./smoke-kit.cjs");

const FIXTURE = path.join(SMOKE_ROOT, "fixtures/video/long_gop_h264.mp4");

module.exports.items = async (window) => {
  const { js, wait } = page(window);
  const shoot = async (name) => {
    await frames({ js });
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };
  const row = (label) =>
    `[...document.querySelectorAll('[role=treeitem]')].find((r) => r.textContent.startsWith(${JSON.stringify(label)}))`;
  const dialogButton = (label) =>
    `[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === ${JSON.stringify(label)})`;
  const menuAt = async (label) => {
    await js(`(() => {
      const box = ${row(label)}.getBoundingClientRect();
      ${row(label)}.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: box.left + 40, clientY: box.top + 8 }));
    })()`);
    await wait("document.querySelector('[role=menu]')");
  };
  const choose = (name) =>
    js(
      `[...document.querySelectorAll('[role=menuitem]')].find((m) => m.textContent === ${JSON.stringify(name)}).click()`,
    );

  const folder = await fs.mkdtemp(path.join(os.tmpdir(), "ax-devil-smoke-items-"));
  const theme = nativeTheme.themeSource;
  try {
    await fs.mkdir(SCREENSHOTS, { recursive: true });
    await setView(window, [1100, 720], 1);
    await js(
      "!document.querySelector('[aria-label=\"Workspace items\"], [data-cursor-id=\"sidebar:add-video\"]') && document.querySelector('[aria-label=Workspace]').click()",
    );
    // A copy of the fixture is added by typing its path.
    {
      const name = "clip.mp4";
      await fs.copyFile(FIXTURE, path.join(folder, name));
      await openAddVideo(window);
      await wait("document.querySelector('dialog[open] input')");
      // The local window may browse for the video and for the blank overlay row's file. With a blank path the dialog
      // shows its failure and still fits the smallest window at 200% zoom.
      await wait(
        "document.querySelector('dialog[open] [aria-label=\"Choose overlay file\"]') && document.querySelector('dialog[open] [aria-label=\"Choose video file\"]')",
      );
      for (const [shown, zoom] of [
        ["light", 1],
        ["dark", 2],
      ]) {
        nativeTheme.themeSource = shown;
        await wait(`matchMedia('(prefers-color-scheme: ${shown})').matches`);
        await setView(window, [640, 480], zoom);
        assert.deepEqual(
          await js(`(${fit.dialog.toString()})()`),
          [],
          `The local Add video dialog must fit the 640x480 window at ${zoom * 100}% zoom`,
        );
      }
      await setView(window, [1100, 720], 1);
      // A capture right after a theme change can still be the previous frame: wait until the dialog is painted in the
      // theme's colours, and capture again until the picture differs from the other theme's.
      let before = null;
      for (const shown of ["light", "dark"]) {
        nativeTheme.themeSource = shown;
        await wait(`matchMedia('(prefers-color-scheme: ${shown})').matches`);
        await wait(
          `(() => {
            const [r, g, b] = getComputedStyle(document.querySelector('dialog[open]')).backgroundColor.match(/[\\d.]+/g).map(Number);
            return (r + g + b) / 3 > 128 === (${JSON.stringify(shown)} === 'light');
          })()`,
        );
        let png;
        for (let attempt = 0; attempt < 20; attempt++) {
          await frames({ js });
          png = (await window.webContents.capturePage()).toPNG();
          if (!before || !png.equals(before)) break;
          await delay(100);
        }
        assert.ok(
          !before || !png.equals(before),
          "The light and dark Add video screenshots must differ",
        );
        await fs.writeFile(path.join(SCREENSHOTS, `add-video-local-${shown}.png`), png);
        before = png;
      }
      await js(`(() => {
        const input = document.querySelector('dialog[open] input');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(path.join(folder, name))});
        input.dispatchEvent(new Event('input', { bubbles: true }));
      })()`);
      await wait(`${dialogButton("Add")}?.disabled === false`);
      await js(`${dialogButton("Add")}.click()`);
      await wait(`!document.querySelector('dialog') && ${row(name)}`);
    }
    for (const shown of ["light", "dark"]) {
      nativeTheme.themeSource = shown;
      await wait(`matchMedia('(prefers-color-scheme: ${shown})').matches`);
      await menuAt("clip.mp4");
      assert.deepEqual(
        await js(
          "[...[...document.querySelectorAll('[role=menu]')].at(-1).querySelectorAll('[role=menuitem]')].map((m) => m.textContent)",
        ),
        ["Open", "Open to the Side", "Information", "Export video", "Edit", "Remove"],
      );
      await shoot(`items-menu-${shown}.png`);
      await choose("Information");
      await wait("document.querySelector('dialog[open] table')");
      assert.match(
        await js("document.querySelector('dialog[open] table').textContent"),
        /seekable video/,
      );
      await shoot(`items-information-${shown}.png`);
      await js(`${dialogButton("Close")}.click()`);
      await wait("!document.querySelector('dialog')");

      await menuAt("clip.mp4");
      await choose("Edit");
      await wait("document.querySelector('dialog[open] h2')?.textContent === 'Edit video'");
      assert.equal(
        await js("document.querySelector('dialog[open] input').value"),
        path.join(folder, "clip.mp4"),
        "Edit starts from the item",
      );
      await shoot(`items-edit-${shown}.png`);
      if (shown === "dark") break;
      await js(`${dialogButton("Cancel")}.click()`);
      await wait("!document.querySelector('dialog')");
    }

    // Saving a new name renames the item; Delete removes the item, leaving the workspace as it was.
    await js(`(() => {
      const name = [...document.querySelectorAll('dialog[open] input')].at(-1);
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(name, 'Door');
      name.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    await wait(`${dialogButton("Save")}?.disabled === false`);
    await js(`${dialogButton("Save")}.click()`);
    await wait(`!document.querySelector('dialog') && ${row("Door")}`);
    assert.equal(await js(`!${row("clip.mp4")}`), true, "The item is renamed, not added");
    await js(`(() => {
      const found = ${row("Door")};
      found.click();
      found.closest('[role=tree]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }));
    })()`);
    await wait(`!${row("Door")}`);
  } finally {
    nativeTheme.themeSource = theme;
    await removeTemp(folder);
  }
};
