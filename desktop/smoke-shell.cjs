// The smoke test's shell scenario: the start panel, the window chrome, the title, and every dialog and menu fit-checked
// at the window minimum and 200% zoom, in both themes.
const { pressShortcut } = require("./shortcut-input.cjs");
const { app, dialog, nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const {
  SCREENSHOTS,
  button,
  frames,
  isolatedState,
  page,
  reload,
  setView,
  until,
} = require("./smoke-kit.cjs");
const { clickVisible, openSettings, settingsPage } = require("./settings-smoke-kit.cjs");
const { fitChecker } = require("./smoke-fit.cjs");

module.exports.run = async ({ window }) => {
  const { js, wait } = page(window);
  const click = (expression) => clickVisible(window, expression);
  const closeMenu = async () => {
    if (await js("Boolean(document.querySelector('[role=menu]'))"))
      await click("document.querySelector('[aria-expanded=true]')");
  };
  const frame = () =>
    js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");
  const shoot = async (name) => {
    await frame();
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };

  // Seed before title edits can start recovery writes; later updates preserve this entry under their file lock.
  // A recent workspace in a deep folder, as a long state folder gives: the start panel still has to fit.
  const state = isolatedState();
  const deep = path.join(
    app.getPath("home"),
    "ax-devil-smoke-recents",
    "a-folder-whose-name-is-far-too-long-for-the-narrow-sidebar",
    "and-another-one-below-it",
    "Quarterly review.ax-devil.workspace",
  );
  const recents = path.join(state, "ax-devil", "recent-workspaces.json");
  await fs.mkdir(path.dirname(deep), { recursive: true });
  await fs.mkdir(path.dirname(recents), { recursive: true });
  await fs.writeFile(recents, JSON.stringify([{ path: deep, used_ms: Date.now() }]));
  const recentRow = `[...document.querySelectorAll('[data-cursor-id^="row:recent:"]')].find(row => row.dataset.cursorId === ${JSON.stringify(`row:recent:${deep}`)})`;
  // The start panel of a fresh page, with no dialog over it, has Open Workspace as its default: a real Enter asks
  // the native Open dialog.
  await reload(window);
  await wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  // Enter reaches the shell's default only once the start panel shows its enabled Open Workspace, nothing has the
  // focus but the page, no dialog is open and the window has the keyboard; a frame later the shell's key handler,
  // which follows the render, is in place.
  await wait(
    `document.querySelector('[data-cursor-id="sidebar:open-workspace"]:not(:disabled)') &&
      document.activeElement === document.body && !document.querySelector('dialog[open]') && document.hasFocus()`,
  );
  await frames({ js });
  const showOpenDialog = dialog.showOpenDialog.bind(dialog);
  let asked = false;
  dialog.showOpenDialog = async () => {
    asked = true;
    return { canceled: true, filePaths: [] };
  };
  pressShortcut(window, "Enter", { character: true });
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
    await click(`${title}.querySelector('button')`);
    await wait(`document.activeElement === ${title}.querySelector('input')`);
    for (const letter of text) window.webContents.insertText(letter);
    await wait(`${title}.querySelector('input').value === ${JSON.stringify(text)}`);
  };
  const press = (chord) => pressShortcut(window, chord);
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
  const density = await js("window.devicePixelRatio");
  pressShortcut(window, "Ctrl+=");
  await wait(`window.devicePixelRatio > ${density}`);
  pressShortcut(window, "Ctrl+0");
  await wait(`window.devicePixelRatio === ${density}`);

  // The smallest window, at normal zoom and at 200%: the View menu, then the Settings and Keyboard Shortcuts dialogs, in both themes.
  const openShortcuts = async () => {
    await closeMenu();
    await click(
      "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'File')",
    );
    await wait("document.querySelector('[role=menu]')");
    await click(
      "[...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent.startsWith('Keyboard Shortcuts'))",
    );
  };
  // Recovery writes may reorder recents after the title edits. Inspect the fixture by identity, not position.
  await reload(window);
  await wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  await wait(recentRow);
  await fitChecker(window);
  // At 640x480 the name, the end of the folder and the date each stay whole and inside the row, and the folder's
  // tooltip holds all of it.
  await setView(window, [640, 480], 1);
  const recent = await js(`(() => {
    const row = ${recentRow};
    const box = row.getBoundingClientRect();
    const inside = (element) => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.left >= box.left - 1 && rect.right <= box.right + 1;
    };
    const folder = row.querySelector("span[title]");
    return {
      name: inside(row.querySelector(".truncate")),
      suffix: inside(folder.lastElementChild) && folder.lastElementChild.scrollWidth <= folder.lastElementChild.clientWidth + 1,
      date: inside(row.lastElementChild) && row.lastElementChild.textContent === "today",
      folder: folder.title,
      truncated: folder.firstElementChild.scrollWidth > folder.firstElementChild.clientWidth,
      oneLine: folder.getBoundingClientRect().height <= parseFloat(getComputedStyle(folder).lineHeight) + 1,
    };
  })()`);
  assert.deepEqual(
    [recent.name, recent.suffix, recent.date],
    [true, true, true],
    "name, folder suffix and date stay whole in the row",
  );
  assert.equal(recent.truncated, true, "the long folder is truncated");
  assert.equal(recent.oneLine, true, "the folder stays on one line");
  assert.equal(
    recent.folder.replaceAll("\\", "/"),
    path.join("~", path.relative(app.getPath("home"), path.dirname(deep))).replaceAll("\\", "/"),
  );
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
    await closeMenu();
    await frame();
    // With the sidebar open, then closed (the welcome area shows).
    for (const sidebar of ["open", "closed"]) {
      assert.deepEqual(
        await js(`(${fit.page.toString()})()`),
        [],
        `The page must fit the 640x480 window at ${zoom * 100}% zoom, sidebar ${sidebar}`,
      );
      if (zoom === 2 || sidebar === "open") await shoot(`layout-${sidebar}-${theme}${suffix}.png`);
      await click("document.querySelector('[aria-label=Workspace]')");
      await frame();
    }
    await click("document.querySelector('[aria-label=Sessions]')");
    await wait(button("Start sharing"));
    assert.deepEqual(
      await js(`(${fit.page.toString()})()`),
      [],
      `The Sessions view must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
    // The Connect dialog with its password field, which a typed server address brings in.
    await click(`${button("Connect to server")}`);
    await wait("document.querySelector('dialog[open] input')");
    window.webContents.insertText("http://192.168.100.200:7340");
    await wait("document.querySelector('dialog[open] input[type=password]')");
    assert.deepEqual(
      await js(`(${fit.page.toString()})()`),
      [],
      `The Connect dialog must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
    await shoot(`connect-${theme}${suffix}.png`);
    await click(
      "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Cancel')",
    );
    await wait("!document.querySelector('dialog')");
    // At 200% an owner's own session is the tallest the Sessions view gets.
    if (zoom === 2) {
      await click(`${button("Start sharing")}`);
      await wait("document.querySelector('aside input[aria-label=\"Join link\"]')");
      assert.deepEqual(
        await js(`(${fit.page.toString()})()`),
        [],
        "An owner's Sessions view must fit the 640x480 window at 200% zoom",
      );
      await shoot(`session-owner-${theme}${suffix}.png`);
      await click(`${button("Stop sharing")}`);
      await wait(button("Start sharing"));
    }
    await click("document.querySelector('[aria-label=Workspace]')");
    if (!(await js("Boolean(document.querySelector('[role=menu]'))")))
      await click(
        "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'View')",
      );
    await wait("document.querySelector('[role=menu]')");
    assert.deepEqual(
      await js(`(${fit.panel.toString()})('[data-cursor-region="popup"]')`),
      [],
      `The View menu stays inside the window at ${zoom * 100}% zoom`,
    );
    await shoot(`menu-${theme}${suffix}.png`);
    for (const { name, open, close = "Cancel" } of [
      {
        name: "settings",
        open: () => openSettings(window),
      },
      ...[
        ["settings-playback", "Playback"],
        ["settings-stream", "Stream defaults"],
        ["settings-analytics", "Analytics"],
        ["settings-scan", "Network scan"],
        ["settings-storage", "Storage"],
        ["settings-advanced", "Advanced"],
      ].map(([name, tab]) => ({
        name,
        open: async () => {
          await openSettings(window);
          await wait("document.querySelector('dialog[open]')");
          await settingsPage(window, tab);
        },
      })),
      { name: "shortcuts", open: openShortcuts },
      {
        name: "about",
        open: async () => {
          await click(
            "[...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'Help')",
          );
          await wait("document.querySelector('[role=menu]')");
          await click(
            "[...document.querySelectorAll('[role=menuitem]')].find((b) => b.textContent === 'About')",
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
      await closeMenu();
      await open();
      await wait("document.querySelector('dialog[open]')");
      assert.deepEqual(
        await js(`(${fit.dialog.toString()})()`),
        [],
        `The ${name} dialog must fit the 640x480 window at ${zoom * 100}% zoom`,
      );
      await shoot(`${name}-${theme}${suffix}.png`);
      await click(
        `[...document.querySelectorAll('dialog button')].find((b) => b.textContent === '${close}')`,
      );
      await wait("!document.querySelector('dialog')");
    }
  }
};
