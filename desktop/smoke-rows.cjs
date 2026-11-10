// The smoke test's tree-row scenario: a lane hidden itself offers Unhide and open and says why Open is off, and a live
// item's row carries the mark of its stream. Screenshots in both themes. The live stream points at this machine's
// own loopback, so nothing leaves it.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { SCREENSHOTS, frames, page, setView } = require("./smoke-kit.cjs");

module.exports.run = async ({ window }) => {
  const { js, wait } = page(window);
  const theme = nativeTheme.themeSource;
  const shoot = async (name) => {
    await frames({ js });
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };
  const setTheme = async (shown) => {
    nativeTheme.themeSource = shown;
    await wait(`matchMedia('(prefers-color-scheme: ${shown})').matches`);
  };
  const row = (label) =>
    `[...document.querySelectorAll('[role=treeitem]')].find((r) => r.textContent.startsWith(${JSON.stringify(label)}))`;
  const button = (label) => `document.querySelector('button[aria-label=${JSON.stringify(label)}]')`;
  const menuAt = async (found) => {
    await js(`(() => {
      const box = ${found}.getBoundingClientRect();
      ${found}.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: box.left + 40, clientY: box.top + 8 }));
    })()`);
    await wait("document.querySelector('[role=menu]')");
  };
  const closeMenu = async () => {
    await js(
      "document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))",
    );
    await wait("!document.querySelector('[role=menu]')");
  };
  const fill = async (label, text) => {
    const control = `[...document.querySelectorAll('dialog[open] label')].find((l) => l.textContent === ${JSON.stringify(label)}).control`;
    await wait(`!!${control}`);
    await js(`${control}.focus()`);
    window.webContents.insertText(text);
    await wait(`${control}.value === ${JSON.stringify(text)}`);
  };

  try {
    await fs.mkdir(SCREENSHOTS, { recursive: true });
    await setView(window, [1100, 720], 1);
    await js(
      "!document.querySelector('[aria-label=\"Workspace items\"], [data-cursor-id=\"sidebar:add-video\"]') && document.querySelector('[aria-label=Workspace]').click()",
    );

    // The MOT Challenge playlist, opened down to its lanes.
    await wait(`!!${row("MOT Challenge")}`);
    for (let pass = 0; pass < 4; pass++)
      await js(`(() => {
        const items = [...document.querySelectorAll('[role=treeitem]')];
        const from = items.findIndex((r) => r.textContent.startsWith('MOT Challenge'));
        const rest = items.slice(from + 1).findIndex((r) => r.getAttribute('aria-level') === '1');
        const inside = items.slice(from, rest < 0 ? undefined : from + 1 + rest);
        inside.find((r) => r.getAttribute('aria-expanded') === 'false')?.querySelector('span[aria-hidden]').click();
      })()`);
    // A lane hidden itself: Open is off with its reason, and Unhide and open follows Open to the Side.
    await wait(`!!${button("Hide DPM")}`);
    await js(`${button("Hide DPM")}.click()`);
    await wait(`!${row("DPM")}`);
    await js(`${button("Show hidden items")}.click()`);
    await wait(`!!${row("DPM")}`);
    for (const shown of ["light", "dark"]) {
      await setTheme(shown);
      await menuAt(row("DPM"));
      assert.deepEqual(
        await js(
          "[...[...document.querySelectorAll('[role=menu]')].at(-1).querySelectorAll('[role=menuitem]')].map((m) => [m.textContent, m.disabled])",
        ),
        [
          ["Open", true],
          ["Open to the Side", true],
          ["Unhide and open", false],
          ["Information", false],
          ["Unhide", false],
        ],
      );
      await shoot(`rows-hidden-lane-menu-${shown}.png`);
      await closeMenu();
    }
    await js(`${button("Unhide DPM")}.click()`);
    await wait(`!!${button("Hide DPM")}`);
    await js(`${button("Show hidden items")}.click()`);

    // A live item: its row is marked with its stream's state.
    await js(
      "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'File').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await js(
      "[...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent.startsWith('Add Live Stream')).click()",
    );
    await wait("document.querySelector('dialog[open] h2')?.textContent === 'Add Live Stream'");
    await fill("Host", "127.0.0.1");
    await fill("Username", "root");
    await fill("Password", "pass");
    // No overlay, so the stream asks for nothing but the video.
    await js(`(() => {
      const label = [...document.querySelectorAll('dialog[open] label')].find((l) => l.textContent === 'Overlay Mode');
      const select = document.getElementById(label.htmlFor);
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, 'none');
      select.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);
    const add =
      "[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'OK')";
    await wait(`${add}?.disabled === false`);
    await js(`${add}.click()`);
    await wait("!document.querySelector('dialog')");
    const live = "document.querySelector('[data-section=live] [role=treeitem]')";
    await wait(`!!${live}`);
    for (const shown of ["light", "dark"]) {
      await setTheme(shown);
      await shoot(`rows-live-mark-${shown}.png`);
    }
    await js(`(() => {
      const found = ${live};
      found.click();
      found.closest('[role=tree]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }));
    })()`);
    await wait(`!${live}`);
  } finally {
    nativeTheme.themeSource = theme;
  }
};
