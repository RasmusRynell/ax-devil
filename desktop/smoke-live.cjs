// The smoke test's Live scenario: the Live sidebar view and the Add Live Stream dialog in each of its four overlay
// modes fit the smallest window at normal zoom and at 200%, in both themes. Nothing is connected: the dialog stays
// unfilled, so the camera is never asked for anything.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const { SCREENSHOTS, dialogSize, frames, page, setView } = require("./smoke-kit.cjs");

// Each overlay mode, with a label only its page of the dialog has (none for the modes whose page is a note).
const MODES = [
  ["none", null],
  ["rtsp", null],
  ["mqtt", "MQTT Port"],
  ["datahub", "Topic Channel"],
];

module.exports.live = async (window) => {
  const { js, wait } = page(window);
  const theme = nativeTheme.themeSource;
  const labelled = (text) =>
    `[...document.querySelectorAll('dialog[open] label')].some((l) => l.textContent === '${text}')`;
  // Picks an option the way a person does: sets the select and tells React it changed.
  const choose = (label, value) =>
    js(`(() => {
      const label = [...document.querySelectorAll('dialog[open] label')].find((l) => l.textContent === '${label}');
      const select = document.getElementById(label.htmlFor);
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(select, '${value}');
      select.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);
  const picked = (label) =>
    `document.getElementById([...document.querySelectorAll('dialog[open] label')].find((l) => l.textContent === '${label}').htmlFor).value`;
  const shoot = async (name) =>
    fs.writeFile(path.join(SCREENSHOTS, name), (await window.webContents.capturePage()).toPNG());

  await fs.mkdir(SCREENSHOTS, { recursive: true });
  await setView(window, [1100, 720], 1);
  await js("document.querySelector('nav [aria-label=Live][aria-pressed=false]')?.click()");
  await wait("document.querySelector('nav [aria-label=Live][aria-pressed=true]')");
  await wait("document.querySelector('aside input[placeholder=Search]')");

  // Six logins added in the scan settings stay (with the saved one), and the list scrolls up and down, never sideways.
  const addLogin =
    "[...document.querySelectorAll('aside button')].find((b) => b.textContent === 'Add login')";
  await wait(`!!${addLogin}`);
  for (let added = 0; added < 6; added++) {
    await js(`${addLogin}.click()`);
    await frames({ js });
  }
  const rowsShown = () =>
    js(`(() => {
      const box = document.querySelector('aside input[aria-label=Username]').closest('.overflow-y-auto');
      const rows = [...box.children];
      return {
        total: rows.length,
        scrolls: box.scrollHeight > box.clientHeight,
        sideways: box.scrollWidth > box.clientWidth,
      };
    })()`);
  await frames({ js });
  assert.deepEqual(await rowsShown(), { total: 7, scrolls: true, sideways: false });
  // Back to the saved login, so the fit checks below see the footer as a person finds it.
  for (let removed = 0; removed < 6; removed++) {
    await js(
      "[...document.querySelectorAll('aside button[aria-label=\"Remove login\"]')].at(-1).click()",
    );
    await frames({ js });
  }
  assert.equal((await rowsShown()).total, 1);

  for (const [shown, zoom] of [
    ["light", 1],
    ["dark", 1],
    ["light", 2],
    ["dark", 2],
  ]) {
    const suffix = zoom === 1 ? "" : "-200";
    const where = `at ${zoom * 100}% zoom in the ${shown} theme`;
    await setView(window, [640, 480], zoom);
    nativeTheme.themeSource = shown;
    await wait(`matchMedia('(prefers-color-scheme: ${shown})').matches`);
    await frames({ js });
    assert.deepEqual(
      await js(`(${fit.page.toString()})()`),
      [],
      `The Live view must fit the 640x480 window ${where}`,
    );
    assert.equal(
      await js(`(() => {
        const input = document.querySelector('aside input[placeholder=Search]');
        const context = document.createElement('canvas').getContext('2d');
        context.font = getComputedStyle(input).font;
        return input.clientWidth >= context.measureText('Search').width;
      })()`),
      true,
      `The Live view's Search placeholder must show whole ${where}`,
    );
    await shoot(`live-view-${shown}${suffix}.png`);

    await js(
      "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'File').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await js(
      "[...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent.startsWith('Add Live Stream')).click()",
    );
    await wait("document.querySelector('dialog[open] h2')?.textContent === 'Add Live Stream'");
    let size;
    for (const [mode, field] of MODES) {
      await choose("Overlay Mode", mode);
      await wait(`${picked("Overlay Mode")} === '${mode}'`);
      if (field) await wait(labelled(field));
      await frames({ js });
      // The dialog is as big as its biggest mode's page, so choosing a mode does not resize it.
      size ??= await dialogSize({ js });
      assert.equal(
        await dialogSize({ js }),
        size,
        `The Add Live Stream dialog must keep its size in the ${mode} mode ${where}`,
      );
      assert.deepEqual(
        await js(`(${fit.dialog.toString()})()`),
        [],
        `The Add Live Stream dialog in the ${mode} mode must fit the 640x480 window ${where}`,
      );
      await shoot(`live-add-${mode}-${shown}${suffix}.png`);
    }
    await js(
      "[...document.querySelectorAll('dialog[open] button')].find((b) => b.textContent === 'Cancel').click()",
    );
    await wait("!document.querySelector('dialog')");
  }

  nativeTheme.themeSource = theme;
  await setView(window, [1100, 720], 1);
  await js("document.querySelector('nav [aria-label=Workspace][aria-pressed=false]')?.click()");
  await wait("document.querySelector('nav [aria-label=Workspace][aria-pressed=true]')");
};
