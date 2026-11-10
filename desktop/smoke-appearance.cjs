// Appearance stays usable at the largest text size, including in a short, zoomed window.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const { SCREENSHOTS, page, setView, frames } = require("./smoke-kit.cjs");

module.exports.run = async ({ window }) => {
  const { js, wait } = page(window);
  const click = (selector, text) =>
    js(
      `[...document.querySelectorAll(${JSON.stringify(selector)})].find(b => b.textContent === ${JSON.stringify(text)}).click()`,
    );
  for (const theme of ["light", "dark"]) {
    for (const zoom of [1, 2]) {
      await setView(window, [1000, 800], 1);
      await click("[data-top]", "Help");
      await wait("document.querySelector('[role=menu]')");
      await click("[role=menuitem]", "Quick Setup");
      await wait("document.querySelector('dialog[open]')");
      await click(
        '[role=radiogroup][aria-label="Theme"] label',
        theme === "light" ? "Light" : "Dark",
      );
      await click('[role=radiogroup][aria-label="Text size"] label', "AaLarger");
      await wait("document.documentElement.dataset.textSize === 'larger'");
      await setView(window, [640, 480], zoom);
      await frames({ js });
      await fs.writeFile(
        path.join(SCREENSHOTS, `appearance-${theme}-${zoom}.png`),
        (await window.webContents.capturePage()).toPNG(),
      );
      assert.deepEqual(
        await js(`(${fit.dialog.toString()})()`),
        [],
        `Quick Setup, ${theme}, ${zoom * 100}%`,
      );
      for (const group of ["Theme", "Text size"]) {
        assert.equal(
          await js(`(() => {
          const row = document.querySelector('[role=radiogroup][aria-label="${group}"]');
          return [...row.querySelectorAll('label')].every(label => {
            label.scrollIntoView({ block: 'nearest' });
            const box = label.getBoundingClientRect();
            const preview = label.querySelector('[aria-hidden=true]').getBoundingClientRect();
            return preview.width > 0 && preview.height > 0 && box.top >= 0 && box.bottom <= innerHeight;
          });
        })()`),
          true,
          `${group} previews remain reachable`,
        );
      }
      await click("dialog button", "Done");
      await wait("!document.querySelector('dialog')");
      await js("document.querySelector('[aria-label=Settings]').click()");
      await wait("document.querySelector('dialog[open]')");
      for (const tab of await js(
        "[...document.querySelectorAll('[role=tab]')].map(b => b.textContent)",
      )) {
        if (zoom === 2) {
          await js(`(() => {
            const select = [...document.querySelectorAll('select')].find(e => e.closest('label').textContent.startsWith('Page'));
            Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, ${JSON.stringify(tab)});
            select.dispatchEvent(new Event('change', { bubbles: true }));
          })()`);
        } else await click("[role=tab]", tab);
        await frames({ js });
        await fs.writeFile(
          path.join(
            SCREENSHOTS,
            `appearance-settings-${tab.replaceAll(" ", "-")}-${theme}-${zoom}.png`,
          ),
          (await window.webContents.capturePage()).toPNG(),
        );
        assert.deepEqual(
          await js(`(${fit.dialog.toString()})()`),
          [],
          `Settings ${tab}, ${theme}, ${zoom * 100}%`,
        );
      }
      await click("dialog button", "Cancel");
      await wait("!document.querySelector('dialog')");
    }
  }
  await setView(window, [1000, 800], 1);
  await click("[data-top]", "Help");
  await wait("document.querySelector('[role=menu]')");
  await click("[role=menuitem]", "Quick Setup");
  await wait("document.querySelector('dialog[open]')");
  await js("document.querySelector('input[value=auto]').click()");
  await js("document.querySelector('input[value=system]').click()");
  await click("dialog button", "Done");
  await wait("!document.querySelector('dialog')");
};
