// Desktop smoke test and screenshot harness, run by `make tests-desktop` on a private display.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const { SCREENSHOTS, button, page, reload, setView, until, watchCsp } = require("./smoke-kit.cjs");
const { playlist } = require("./smoke-playlist.cjs");
const { exportDialog } = require("./smoke-export.cjs");
const { remote } = require("./smoke-remote.cjs");
const { sharing } = require("./smoke-sharing.cjs");
const { video } = require("./smoke-video.cjs");

module.exports = async (window, shell) => {
  const { js, wait } = page(window);
  await wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  assert.equal(await js("document.querySelector('[role=alert]')"), null);

  // No page of the release app may break its Content Security Policy. A reload covers the load itself; a request the
  // policy forbids shows the collector sees violations at all.
  const ownerCsp = watchCsp(window);
  await reload(window);
  await wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  await js("fetch('https://example.invalid/').catch(() => {})");
  await until(() => ownerCsp.length > 0, "the policy to refuse a request to another origin");
  ownerCsp.length = 0;

  await fs.mkdir(SCREENSHOTS, { recursive: true });
  const frame = () =>
    js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");
  const shoot = async (name) => {
    await frame();
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };

  // The isolated home is a first run, so Quick Setup is open: check it in the smallest window, then Done closes it.
  const size = window.getSize();
  await wait("document.querySelector('dialog[open] h2')?.textContent === 'Quick Setup'");
  for (const [theme, zoom] of [
    ["light", 1],
    ["dark", 1],
    ["light", 2],
    ["dark", 2],
  ]) {
    await setView(window, [640, 480], zoom);
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    await frame();
    assert.deepEqual(
      await js(`(${fit.dialog.toString()})()`),
      [],
      `Quick Setup must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
    await shoot(`quick-setup-${theme}${zoom === 1 ? "" : "-200"}.png`);
  }
  window.webContents.setZoomFactor(1);
  await js(
    "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Done').click()",
  );
  await wait("!document.querySelector('dialog')");
  window.setSize(...size);

  const backgrounds = {};
  for (const theme of ["light", "dark"]) {
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    backgrounds[theme] = await js("getComputedStyle(document.body).backgroundColor");
    // Capturing before the compositor has produced a frame for the new theme fails.
    await js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");
    const image = await window.webContents.capturePage();
    await fs.writeFile(path.join(SCREENSHOTS, `main-${theme}.png`), image.toPNG());
  }
  assert.notEqual(backgrounds.light, backgrounds.dark, "The window must follow the theme");

  // The title bar has the window controls, and Ctrl+= and Ctrl+0 zoom the page.
  for (const name of ["Minimize", "Maximize", "Close"])
    assert.notEqual(await js(`document.querySelector('[aria-label=${name}]')`), null, name);
  const press = (keyCode) =>
    window.webContents.sendInputEvent({ type: "keyDown", keyCode, modifiers: ["control"] });
  press("=");
  await wait("window.devicePixelRatio > 1");
  press("0");
  await wait("window.devicePixelRatio === 1");

  // The smallest window, at normal zoom and at 200%: the View menu, then the Settings and Keyboard Shortcuts dialogs, in both themes.
  const openShortcuts = async () => {
    await js(
      "document.querySelector('[role=menu]') && document.querySelector('[aria-expanded=true]').click()",
    );
    await js(
      "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'File').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await js(
      "[...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent.startsWith('Keyboard Shortcuts')).click()",
    );
  };
  for (const [theme, zoom] of [
    ["light", 1],
    ["dark", 1],
    ["light", 2],
    ["dark", 2],
  ]) {
    await setView(window, [640, 480], zoom);
    const suffix = zoom === 1 ? "" : "-200";
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    // Nothing is open: the page must fit the window, and every control must be reachable.
    await js(
      "document.querySelector('[role=menu]') && document.querySelector('[aria-expanded=true]').click()",
    );
    await frame();
    // With the sidebar open, then closed (the welcome area shows).
    for (const sidebar of ["open", "closed"]) {
      assert.deepEqual(
        await js(`(${fit.page.toString()})()`),
        [],
        `The page must fit the 640x480 window at ${zoom * 100}% zoom, sidebar ${sidebar}`,
      );
      if (zoom === 2) await shoot(`layout-${sidebar}-${theme}${suffix}.png`);
      await js("document.querySelector('[aria-label=Workspace]').click()");
      await frame();
    }
    await js("document.querySelector('[aria-label=Sessions]').click()");
    await wait(button("Start sharing"));
    assert.deepEqual(
      await js(`(${fit.page.toString()})()`),
      [],
      `The Sessions view must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
    // The Connect dialog with its password field, which a typed server address brings in.
    await js(`${button("Connect to server")}.click()`);
    await wait("document.querySelector('dialog[open] input')");
    window.webContents.insertText("http://192.168.100.200:7340");
    await wait("document.querySelector('dialog[open] input[type=password]')");
    assert.deepEqual(
      await js(`(${fit.page.toString()})()`),
      [],
      `The Connect dialog must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
    await shoot(`connect-${theme}${suffix}.png`);
    await js(
      "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Cancel').click()",
    );
    await wait("!document.querySelector('dialog')");
    // At 200% an owner's own session is the tallest the Sessions view gets.
    if (zoom === 2) {
      await js(`${button("Start sharing")}.click()`);
      await wait("document.querySelector('aside input[aria-label=\"Join link\"]')");
      assert.deepEqual(
        await js(`(${fit.page.toString()})()`),
        [],
        "An owner's Sessions view must fit the 640x480 window at 200% zoom",
      );
      await shoot(`session-owner-${theme}${suffix}.png`);
      await js(`${button("Stop sharing")}.click()`);
      await wait(button("Start sharing"));
    }
    await js("document.querySelector('[aria-label=Workspace]').click()");
    await js(
      "!document.querySelector('[role=menu]') && [...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'View').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await shoot(`menu-${theme}${suffix}.png`);
    for (const { name, open } of [
      {
        name: "settings",
        open: () => js("document.querySelector('[aria-label=Settings]').click()"),
      },
      {
        name: "settings-storage",
        open: async () => {
          await js("document.querySelector('[aria-label=Settings]').click()");
          await wait("document.querySelector('dialog[open]')");
          await js(
            "[...document.querySelectorAll('[role=tab]')].find((b) => b.textContent === 'Storage').click()",
          );
        },
      },
      { name: "shortcuts", open: openShortcuts },
    ]) {
      await js(
        "document.querySelector('[role=menu]') && document.querySelector('[aria-expanded=true]').click()",
      );
      await open();
      await wait("document.querySelector('dialog[open]')");
      assert.deepEqual(
        await js(`(${fit.dialog.toString()})()`),
        [],
        `The ${name} dialog must fit the 640x480 window at ${zoom * 100}% zoom`,
      );
      await shoot(`${name}-${theme}${suffix}.png`);
      await js(
        "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Cancel').click()",
      );
      await wait("!document.querySelector('dialog')");
    }
  }

  await video(window);
  await exportDialog(window);
  await playlist(window);
  await sharing(window, shell, ownerCsp);
  await remote(window, shell);
  console.log(
    `PASS: app shell loaded; screenshots in ${path.relative(path.resolve(__dirname, ".."), SCREENSHOTS)}`,
  );
};
