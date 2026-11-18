// Appearance stays usable at the largest text size, including in a short, zoomed window.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const { SCREENSHOTS, page, setView, frames } = require("./smoke-kit.cjs");

const {
  clickVisible,
  openSettings,
  settingsPage,
  focusedSettingsPage,
} = require("./settings-smoke-kit.cjs");

module.exports.run = async ({ window }) => {
  const { js, wait } = page(window);
  const click = (selector, text) =>
    clickVisible(
      window,
      `[...document.querySelectorAll(${JSON.stringify(selector)})].find(b => b.textContent === ${JSON.stringify(text)})`,
    );
  for (const size of ["system", "larger"]) {
    const suffix = size === "system" ? "-normal" : "";
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
        await clickVisible(
          window,
          `document.querySelector('input[value="${size}"]').closest("label")`,
        );
        await wait(`document.documentElement.dataset.textSize === "${size}"`);
        await setView(window, [640, 480], zoom);
        await frames({ js });
        await fs.writeFile(
          path.join(SCREENSHOTS, `appearance-${theme}-${zoom}${suffix}.png`),
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
        await openSettings(window);
        await wait("document.querySelector('dialog[open]')");
        await settingsPage(window, "Advanced");
        await setView(window, [900, 700], 1);
        await focusedSettingsPage(window, "Advanced");
        await setView(window, [640, 480], zoom);
        await focusedSettingsPage(window, "Advanced");
        let pageLayout;
        for (const tab of await js(
          "[...document.querySelectorAll('[role=tab]')].map(b => b.textContent)",
        )) {
          await settingsPage(window, tab);
          if (tab === "Playback") {
            await js(
              `(() => { const select = document.querySelector('[role=tabpanel] select'); select.value = 'manual'; select.dispatchEvent(new Event('change', { bubbles: true })); })()`,
            );
            await wait("document.querySelector('[role=tabpanel] input[role=spinbutton]')");
            assert.equal(
              await js(
                `(() => { const panel = document.querySelector('[role=tabpanel]'); let box = panel; while (getComputedStyle(box).overflowY !== 'auto') box = box.parentElement; const view = box.getBoundingClientRect(); return [...panel.querySelectorAll('input, select, button')].every((c) => { const r = c.getBoundingClientRect(); return r.top >= view.top - 1 && r.bottom <= view.bottom + 1; }); })()`,
              ),
              true,
              `Playback controls are in view without scrolling at ${zoom * 100}%`,
            );
          }
          await frames({ js });
          // Every page has the one height: the dialog, the page list and the buttons stay put between pages.
          const layout = await js(
            `(() => { const d = document.querySelector('dialog[open]'); const at = (e) => { const b = e.getBoundingClientRect(); return b.top + ' ' + b.height; }; return [d, d.querySelector('[role=tablist]'), d.querySelector('form').lastElementChild].map(at).join(' | '); })()`,
          );
          pageLayout ??= layout;
          assert.equal(
            layout,
            pageLayout,
            `Settings ${tab} keeps the dialog, page list and buttons in place, ${theme}, ${zoom * 100}%`,
          );
          await frames({ js });
          await fs.writeFile(
            path.join(
              SCREENSHOTS,
              `appearance-settings-${tab.replaceAll(" ", "-")}-${theme}-${zoom}${suffix}.png`,
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
  }
  await setView(window, [1000, 800], 1);
  await click("[data-top]", "Help");
  await wait("document.querySelector('[role=menu]')");
  await click("[role=menuitem]", "Quick Setup");
  await wait("document.querySelector('dialog[open]')");
  await clickVisible(window, "document.querySelector('input[value=auto]').closest('label')");
  await clickVisible(window, "document.querySelector('input[value=system]').closest('label')");
  await click("dialog button", "Done");
  await wait("!document.querySelector('dialog')");
};
