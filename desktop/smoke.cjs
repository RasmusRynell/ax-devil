// Desktop smoke test and screenshot harness, run by `make test-desktop` on a private display.
const { dialog, nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const {
  SCREENSHOTS,
  SMOKE_ROOT,
  button,
  page,
  reload,
  setView,
  until,
  watchCsp,
} = require("./smoke-kit.cjs");
const { gallery } = require("./smoke-gallery.cjs");
const { items } = require("./smoke-items.cjs");
const { rows } = require("./smoke-rows.cjs");
const { sidebar } = require("./smoke-sidebar.cjs");
const { playlist } = require("./smoke-playlist.cjs");
const { split } = require("./smoke-split.cjs");
const { plugins } = require("./smoke-plugins.cjs");
const { bookmarks } = require("./smoke-bookmarks.cjs");
const { exportDialog } = require("./smoke-export.cjs");
const { live } = require("./smoke-live.cjs");
const { remote } = require("./smoke-remote.cjs");
const { handedOff } = require("./smoke-handoff.cjs");
const { sharing } = require("./smoke-sharing.cjs");
const { reload: reloadOffer } = require("./smoke-reload.cjs");
const { video } = require("./smoke-video.cjs");

module.exports = async (window, shell) => {
  const { js, wait } = page(window);
  await wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  assert.equal(await js("document.querySelector('[role=alert]')"), null);

  // No page of the app may break its Content Security Policy. A reload covers the load itself; a request the
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
    if (zoom === 1) {
      // The largest text size previews live and the dialog still fits; System goes back before Done stores it.
      const textSize = async (value) => {
        await js(`document.querySelector("input[type=radio][value=${value}]").click()`);
        await wait(`document.documentElement.dataset.textSize === "${value}"`);
        await frame();
      };
      await textSize("larger");
      assert.deepEqual(
        await js(`(${fit.dialog.toString()})()`),
        [],
        "Quick Setup must fit the 640x480 window at the largest text size",
      );
      await shoot(`quick-setup-larger-${theme}.png`);
      await textSize("system");
    }
  }
  window.webContents.setZoomFactor(1);
  await js(
    "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Done').click()",
  );
  await wait("!document.querySelector('dialog')");
  window.setSize(...size);

  // The start panel of a fresh page, with no dialog over it, has Open Workspace as its default: a real Enter asks
  // the native Open dialog.
  await reload(window);
  await wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  const showOpenDialog = dialog.showOpenDialog.bind(dialog);
  let asked = false;
  dialog.showOpenDialog = async () => {
    asked = true;
    return { canceled: true, filePaths: [] };
  };
  for (const type of ["keyDown", "char", "keyUp"])
    window.webContents.sendInputEvent({ type, keyCode: type === "char" ? "\r" : "Enter" });
  await until(() => asked, "Enter to open the Open Workspace dialog");
  dialog.showOpenDialog = showOpenDialog;

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

  // The workspace is named from its title: typing is sent as it goes, Escape takes the text back, Enter keeps it.
  const title = "document.querySelector('[aria-label=Sidebar] h2')";
  const editTitle = async (text) => {
    await js(`${title}.querySelector('button').click()`);
    await wait(`document.activeElement === ${title}.querySelector('input')`);
    for (const letter of text) window.webContents.insertText(letter);
    await wait(`${title}.querySelector('input').value === ${JSON.stringify(text)}`);
  };
  const press = (keyCode) =>
    ["keyDown", "keyUp"].forEach((type) => window.webContents.sendInputEvent({ type, keyCode }));
  await editTitle("Review");
  await wait("document.title.startsWith('● ')");
  press("Escape");
  await wait(`${title}.textContent === 'Untitled' && document.title === 'Untitled — ax-devil'`);
  assert.equal(await js("document.querySelector('[aria-label=\"Unsaved changes\"]')"), null);
  await editTitle("Review");
  press("Enter");
  await wait("document.title === '● Review — ax-devil'");
  for (const theme of ["light", "dark"]) {
    nativeTheme.themeSource = theme;
    await wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
    await shoot(`workspace-name-${theme}.png`);
  }
  await editTitle("Untitled");
  press("Enter");
  await wait("document.title === 'Untitled — ax-devil'");

  // The title bar has the window controls, and Ctrl+= and Ctrl+0 zoom the page. A window that fills a small screen
  // counts as maximized, and then its second control is Restore.
  for (const selector of [
    "[aria-label=Minimize]",
    "[aria-label=Maximize], [aria-label=Restore]",
    "[aria-label=Close]",
  ])
    assert.ok(await js(`document.querySelector("${selector}") !== null`), selector);
  const zoomKey = (keyCode) =>
    window.webContents.sendInputEvent({ type: "keyDown", keyCode, modifiers: ["control"] });
  const density = await js("window.devicePixelRatio");
  zoomKey("=");
  await wait(`window.devicePixelRatio > ${density}`);
  zoomKey("0");
  await wait(`window.devicePixelRatio === ${density}`);

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
    for (const { name, open, close = "Cancel" } of [
      {
        name: "settings",
        open: () => js("document.querySelector('[aria-label=Settings]').click()"),
      },
      ...[
        ["settings-stream", "Stream defaults"],
        ["settings-analytics", "Analytics"],
        ["settings-scan", "Network scan"],
        ["settings-storage", "Storage"],
        ["settings-advanced", "Advanced"],
      ].map(([name, tab]) => ({
        name,
        open: async () => {
          await js("document.querySelector('[aria-label=Settings]').click()");
          await wait("document.querySelector('dialog[open]')");
          await js(
            `[...document.querySelectorAll('[role=tab]')].find((b) => b.textContent === '${tab}').click()`,
          );
        },
      })),
      { name: "shortcuts", open: openShortcuts },
      {
        name: "about",
        open: async () => {
          await js(
            "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'Help').click()",
          );
          await wait("document.querySelector('[role=menu]')");
          await js(
            "[...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent === 'About').click()",
          );
        },
        close: "OK",
      },
      {
        name: "error",
        // An error nobody catches, with its details open.
        open: async () => {
          await js("setTimeout(() => { throw new Error('Smoke test failure'); })");
          await wait("document.querySelector('dialog details')");
          await js("document.querySelector('dialog details').open = true");
        },
        close: "Continue",
      },
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
        `[...document.querySelectorAll('dialog button')].find((b) => b.textContent === '${close}').click()`,
      );
      await wait("!document.querySelector('dialog')");
    }
  }

  console.log("smoke: live");
  await live(window);
  console.log("smoke: kit gallery");
  await gallery(window);
  console.log("smoke: video");
  await video(window);
  console.log("smoke: export");
  await exportDialog(window);
  console.log("smoke: bookmarks");
  await bookmarks(window);
  console.log("smoke: playlist");
  await playlist(window);
  console.log("smoke: split");
  await split(window);
  console.log("smoke: reload offer");
  await reloadOffer();
  console.log("smoke: plugins");
  await plugins(window);
  console.log("smoke: items");
  await items(window);
  console.log("smoke: tree rows");
  await rows(window);
  console.log("smoke: sidebar");
  await sidebar(window);
  console.log("smoke: sharing");
  await sharing(window, shell, ownerCsp);
  console.log("smoke: remote");
  await remote(window, shell);
  console.log("smoke: second launch");
  await handedOff(shell);
  console.log(`PASS: app shell loaded; screenshots in ${path.relative(SMOKE_ROOT, SCREENSHOTS)}`);
};
