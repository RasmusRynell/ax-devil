// Desktop smoke test and screenshot harness, run by `make tests-desktop` on a private display.
const { nativeTheme } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const fit = require("./fit.cjs");
const {
  SCREENSHOTS,
  button,
  center,
  click,
  delay,
  page,
  reload,
  tag,
  until,
  watchCsp,
} = require("./smoke-kit.cjs");
const { remote } = require("./smoke-remote.cjs");

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

  // The smallest window, at normal zoom and at 200%: the View menu, then the Settings dialog, in both themes.
  window.setSize(640, 480);
  const frame = () =>
    js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");
  const shoot = async (name) => {
    await frame();
    await fs.writeFile(
      path.join(SCREENSHOTS, name),
      (await window.webContents.capturePage()).toPNG(),
    );
  };
  for (const [theme, zoom] of [
    ["light", 1],
    ["dark", 1],
    ["light", 2],
    ["dark", 2],
  ]) {
    window.webContents.setZoomFactor(zoom);
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
        await js(`(${fit.toString()})()`),
        [],
        `The page must fit the 640x480 window at ${zoom * 100}% zoom, sidebar ${sidebar}`,
      );
      if (zoom === 2) await shoot(`layout-${sidebar}-${theme}${suffix}.png`);
      await js("document.querySelector('[aria-label=Workspace]').click()");
      await frame();
    }
    await js("document.querySelector('[aria-label=Sessions]').click()");
    await wait(button("Create session"));
    assert.deepEqual(
      await js(`(${fit.toString()})()`),
      [],
      `The Sessions view must fit the 640x480 window at ${zoom * 100}% zoom`,
    );
    // The Connect dialog with its password field, which a typed server address brings in.
    await js(`${button("Connect to server")}.click()`);
    await wait("document.querySelector('dialog[open] input')");
    window.webContents.insertText("http://192.168.100.200:7340");
    await wait("document.querySelector('dialog[open] input[type=password]')");
    assert.deepEqual(
      await js(`(${fit.toString()})()`),
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
      await js(`${button("Create session")}.click()`);
      await wait("document.querySelector('aside input[aria-label=\"Join link\"]')");
      assert.deepEqual(
        await js(`(${fit.toString()})()`),
        [],
        "An owner's Sessions view must fit the 640x480 window at 200% zoom",
      );
      await shoot(`session-owner-${theme}${suffix}.png`);
      await js(`${button("Stop sharing")}.click()`);
      await wait(button("Create session"));
    }
    await js("document.querySelector('[aria-label=Workspace]').click()");
    await js(
      "!document.querySelector('[role=menu]') && [...document.querySelectorAll('[data-top]')].find((b) => b.textContent === 'View').click()",
    );
    await wait("document.querySelector('[role=menu]')");
    await shoot(`menu-${theme}${suffix}.png`);
    await js("document.querySelector('[aria-label=Settings]').click()");
    await wait("document.querySelector('dialog[open]')");
    await shoot(`settings-${theme}${suffix}.png`);
    await js(
      "[...document.querySelectorAll('dialog button')].find((b) => b.textContent === 'Cancel').click()",
    );
    await wait("!document.querySelector('dialog')");
  }

  // A real video through the real backend: the canvas shows a picture, and stepping changes it.
  const video = path.resolve(__dirname, "../fixtures/video/long_gop_h264.mp4");
  window.setSize(1100, 720);
  window.webContents.setZoomFactor(1);
  assert.equal(
    await js(`fetch("/api/workspace/items", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ items: [{ type: "Video", path: ${JSON.stringify(video)} }], open: true }),
    }).then((response) => response.ok)`),
    true,
  );
  const shownFrame = `Number.parseInt(document.querySelector("output[aria-label=Frame]")?.textContent ?? "")`;
  await wait(`${shownFrame} === 0`);
  // Frame N of this video is flat with luma 7 N, so frame 0 is black and frame 12 a visible grey.
  const centre = async () => {
    await frame();
    const image = await window.webContents.capturePage();
    const [x, y, w, h] = await js(
      "(() => { const r = document.querySelector('canvas').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()",
    );
    const scale = image.getSize().width / (await js("innerWidth"));
    const bitmap = image.toBitmap();
    const at =
      (Math.round((y + h / 2) * scale) * image.getSize().width + Math.round((x + w / 2) * scale)) *
      4;
    return Math.max(bitmap[at], bitmap[at + 1], bitmap[at + 2]);
  };
  await js("document.querySelector('[aria-label=Pause]')?.click()");
  const black = await centre();
  for (let step = 1; step <= 12; step++) {
    await js("document.querySelector('[aria-label=\"Forward 1 frame\"]').click()");
    await wait(`${shownFrame} === ${step}`);
  }
  const grey = await centre();
  assert.ok(black < 16, `Frame 0 of the video is black, got ${black}`);
  assert.ok(grey > 40, `Frame 12 must be a visible grey, got ${grey}: the canvas is still black`);

  // Sharing is off until someone asks: the first window is private, and its backend holds no session. Creating one
  // in the Sessions view gives a join link, which a second window opens as a watcher.
  window.webContents.setZoomFactor(1);
  await js(
    "document.querySelector('[role=menu]') && document.querySelector('[aria-expanded=true]').click()",
  );
  window.setBounds({ x: 0, y: 0, width: 1000, height: 700 });
  const sessions = () => js("fetch('/api/share/sessions').then((r) => r.json())");
  const frames = (driver) =>
    driver.js("new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))");
  assert.deepEqual(await sessions(), []);
  await js("document.querySelector('[aria-label=Sessions]').click()");
  await wait(button("Create session"));
  await js(`${button("Create session")}.click()`);
  const joinLink = "document.querySelector('aside input[aria-label=\"Join link\"]')";
  await wait(joinLink);
  const link = await js(`${joinLink}.value`);
  assert.match(link, /^http:\/\/127\.0\.0\.1:\d+\/#join=/);
  assert.equal((await sessions()).length, 1);

  const other = await shell.showRemote(link);
  other.setBounds({ x: 40, y: 40, width: 760, height: 560 });
  const second = page(other);
  await second.wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  // The watcher shows the owner's view, the Sessions view, with both of them in it.
  const participants = "document.querySelectorAll('aside [aria-label=\"This session\"] li').length";
  await second.wait("document.querySelector('aside h2')?.textContent === 'Sessions'");
  await second.wait(`${participants} === 2`);
  await wait(`${participants} === 2`);

  // Reloading the watcher takes its seat back: same Guest N, and the owner still sees two participants.
  const watcherCsp = watchCsp(other);
  const you =
    "[...document.querySelectorAll('aside [aria-label=\"This session\"] li')].find((li) => li.textContent.includes('(you)'))?.textContent";
  const guest = await second.js(you);
  assert.match(guest, /^Guest \d+ \(you\)$/);
  await reload(other);
  await second.wait(`${participants} === 2`);
  assert.equal(await second.js(you), guest, "A reload must keep the watcher's seat");
  await delay(500);
  assert.equal(await js(participants), 2);
  const shoot2 = async (name, theme, windows) => {
    nativeTheme.themeSource = theme;
    for (const [suffix, win, driver] of windows) {
      await driver.wait(`matchMedia('(prefers-color-scheme: ${theme})').matches`);
      await frames(driver);
      await fs.writeFile(
        path.join(SCREENSHOTS, `${name}${suffix}-${theme}.png`),
        (await win.webContents.capturePage()).toPNG(),
      );
    }
  };
  const both = [
    ["-window-1", window, { js, wait }],
    ["-window-2", other, second],
  ];

  for (const theme of ["light", "dark"]) await shoot2("sessions-view", theme, both);

  // The share menu holds the cursor options and the way out.
  await js("document.querySelector('[aria-label=Session]').click()");
  await wait("document.querySelector('[role=menu]')");
  for (const theme of ["light", "dark"])
    await shoot2("share-menu", theme, [["-window-1", window, { js, wait }]]);
  await js("document.querySelector('[aria-label=Session]').click()");
  await wait("!document.querySelector('[role=menu]')");

  // A following watcher's clicks change nothing: its controls are disabled, and the owner's window is as it was.
  const sidebarTitle = (title) => `document.querySelector('aside h2')?.textContent === '${title}'`;
  const follow =
    "[...document.querySelectorAll('header button')].find((b) => b.textContent === 'Follow presenter')";
  const ownerView = () =>
    js(`JSON.stringify({
      title: document.querySelector('aside h2')?.textContent,
      pressed: [...document.querySelectorAll('nav[aria-label=Activity] [aria-pressed=true]')].map((b) => b.ariaLabel),
      explore: document.querySelector('aside input[type=checkbox]').checked,
    })`);
  const ownerBefore = await ownerView();
  assert.equal(await second.js("document.querySelector('[aria-label=Bookmarks]').disabled"), true);
  for (const id of ["activity:bookmarks", "activity:live", "activity:workspace"])
    await click(other, id);
  await delay(500);
  assert.equal(
    await ownerView(),
    ownerBefore,
    "A following watcher must not change the owner's view",
  );
  assert.ok(
    await second.js(sidebarTitle("Sessions")),
    "A following watcher keeps the owner's view",
  );
  assert.equal(await second.js(`!!${follow}`), false);

  // With exploring allowed, a watcher that touches its own view starts exploring and gets the way back to the
  // presenter, while the owner's view is untouched; turning it off sends every explorer back.
  const explore = "document.querySelector('aside input[type=checkbox]').click()";
  await js(explore);
  await second.wait("document.querySelector('[aria-label=Bookmarks]').disabled === false");
  await second.js("document.querySelector('[aria-label=Bookmarks]').click()");
  await second.wait(follow);
  await second.wait(sidebarTitle("Bookmarks"));
  assert.ok(await js(sidebarTitle("Sessions")), "Exploring must not change the owner's view");
  for (const theme of ["light", "dark"])
    await shoot2("session-exploring", theme, [["", other, second]]);
  await second.js(`${follow}.click()`);
  await second.wait(sidebarTitle("Sessions"));
  await second.wait(`!${follow}`);
  assert.equal((await ownerView()).includes('"explore":true'), true);
  await js(explore);

  // The owner's choice of view is the watcher's, and the watcher cannot make its own.
  await js("document.querySelector('[aria-label=Live]').click()");
  await second.wait("document.querySelector('aside h2')?.textContent === 'Live'");
  assert.equal(await second.js("document.querySelector('[aria-label=Bookmarks]').disabled"), true);

  // The owner's pointer over a control draws a cursor on the same control in the watcher's narrower window.
  for (const id of ["menu:View", "activity:sessions", "activity:bookmarks"]) {
    window.webContents.sendInputEvent({ type: "mouseLeave", x: 0, y: 0 });
    await second.wait(`!${tag("Guest 1")}`);
    const spot = await js(center(id));
    window.webContents.sendInputEvent({ type: "mouseMove", x: spot.x - 3, y: spot.y });
    window.webContents.sendInputEvent({ type: "mouseMove", x: spot.x, y: spot.y });
    await second.wait(tag("Guest 1"));
    const expected = await second.js(center(id));
    let drawn;
    await until(
      async () => {
        drawn = await second.js(
          `(() => { const r = ${tag("Guest 1")}.parentElement.getBoundingClientRect(); return { x: r.left, y: r.top }; })()`,
        );
        return Math.abs(drawn.x - expected.x) <= 2 && Math.abs(drawn.y - expected.y) <= 2;
      },
      `the cursor on ${id} (drawn at ${JSON.stringify(drawn)}, expected ${JSON.stringify(expected)})`,
    );
    if (id === "menu:View")
      for (const theme of ["light", "dark"]) await shoot2("session-cursor-menu", theme, both);
  }

  // A pointer that leaves the window takes its cursor with it. Stopping tells the watcher, and the owner is
  // private again.
  window.webContents.sendInputEvent({ type: "mouseLeave", x: 0, y: 0 });
  await second.wait(`!${tag("Guest 1")}`);
  await js("document.querySelector('[aria-label=Sessions]').click()");
  await wait(button("Stop sharing"));
  await js(`${button("Stop sharing")}.click()`);
  await second.wait(
    "document.querySelector('[role=alert]')?.textContent.includes('stopped sharing')",
  );
  for (const theme of ["light", "dark"])
    await shoot2("session-ended", theme, [["", other, second]]);
  await wait(button("Create session"));
  assert.deepEqual(await sessions(), []);
  assert.deepEqual([ownerCsp, watcherCsp], [[], []], "The release app must break no CSP rule");
  other.close();

  await remote(window, shell);
  console.log(
    `PASS: app shell loaded; screenshots in ${path.relative(path.resolve(__dirname, ".."), SCREENSHOTS)}`,
  );
};
