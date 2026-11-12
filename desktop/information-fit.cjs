// The item Information dialog with real, accepted plugin metadata near the per-object budget.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const { frames, page, setView, SCREENSHOTS, SMOKE_ROOT } = require("./smoke-kit.cjs");

module.exports.run = async (window) => {
  const { js, wait } = page(window);
  const plugin = path.join(SMOKE_ROOT, "fixtures/plugins/information");
  const post = (route, body) =>
    js(`fetch(${JSON.stringify(route)}, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(${JSON.stringify(body)})
  }).then(async r => ({ ok: r.ok, body: await r.text() }))`);
  const row = `[...document.querySelectorAll('[role=treeitem]')].find(r => r.textContent.startsWith('Calibration'))`;
  const button = (label) =>
    `[...document.querySelectorAll('dialog[open] button')].find(b => b.textContent === ${JSON.stringify(label)})`;
  const installed = await post("/api/plugins/install", { path: plugin });
  assert.ok(installed.ok, installed.body);
  try {
    await setView(window, [1100, 720], 1);
    await js(
      "[...document.querySelectorAll('[data-top]')].find(b => b.textContent === 'File').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await js(
      "[...document.querySelectorAll('[role=menu] [role=menuitem]')].find(b => b.textContent.startsWith('Add Playlist')).click()",
    );
    await wait("document.querySelector('dialog[open] select option[value=information]')");
    await js(`(() => {
      const select = document.querySelector('dialog[open] select');
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, 'information');
      select.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);
    await wait(`${button("Load Playlist")}?.disabled === false`);
    await js(`${button("Load Playlist")}.click()`);
    await wait(`!document.querySelector('dialog') && ${row}`);
    await js(`(() => {
      const row = ${row}; const box = row.getBoundingClientRect();
      row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: box.left + 40, clientY: box.top + 8 }));
    })()`);
    await wait("document.querySelector('[role=menu]')");
    await js(
      "[...document.querySelectorAll('[role=menu] [role=menuitem]')].find(b => b.textContent === 'Information').click()",
    );
    await wait(
      "document.querySelector('dialog[open] table')?.textContent.includes('Recorded at the loading dock')",
    );
    const measurements = [];
    // Capture ordinary sizes first; then prove the minimum window at both zoom levels in both themes.
    for (const [width, height, zoom] of [
      [1100, 720, 1],
      [640, 480, 1],
      [640, 480, 2],
    ]) {
      await setView(window, [width, height], zoom);
      for (const theme of ["light", "dark"]) {
        nativeTheme.themeSource = theme;
        await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
        await frames({ js });
        const measure = await js(`(() => {
          const dialog = document.querySelector('dialog[open]');
          const content = dialog.querySelector('table').parentElement;
          content.scrollTop = content.scrollHeight;
          return { problems: (${fit.dialog.toString()})(),
            window: [innerWidth, innerHeight], dialog: dialog.getBoundingClientRect().toJSON(),
            client: [content.clientWidth, content.clientHeight],
            scroll: [content.scrollWidth, content.scrollHeight], scrolled: content.scrollTop };
        })()`);
        measurements.push({ width, height, zoom, theme, ...measure });
        await js("document.querySelector('dialog[open] table').parentElement.scrollTop = 0");
        await frames({ js });
        await fs.writeFile(
          path.join(SCREENSHOTS, `information-long-${theme}-${width}-${zoom * 100}.png`),
          (await window.webContents.capturePage()).toPNG(),
        );
      }
    }
    await fs.writeFile(
      path.join(SCREENSHOTS, "information-fit.json"),
      JSON.stringify(measurements, null, 2),
    );
    for (const measurement of measurements) {
      assert.deepEqual(measurement.problems, [], JSON.stringify(measurement));
      assert.ok(
        measurement.scroll[1] > measurement.client[1] && measurement.scrolled > 0,
        "long metadata scrolls inside the dialog",
      );
      assert.ok(
        measurement.scroll[0] <= measurement.client[0] + 1,
        "the long path wraps without horizontal scrolling",
      );
    }
    await js(`${button("Close")}.click()`);
    await wait("!document.querySelector('dialog')");
    await setView(window, [1100, 720], 1);
    await js(`${row}.click()`);
    await wait(`${row}.closest('[role=tree]').getAttribute('aria-activedescendant') === ${row}.id`);
    await js(
      `${row}.closest('[role=tree]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true }))`,
    );
    await wait(`!${row}`);
  } finally {
    await post("/api/plugins/remove", { target: "information" });
  }
};
