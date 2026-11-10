// The smoke test's sharing scenario: an owner starts sharing, a watcher joins through the link in a second window,
// and a third window lists the shared Session. Screenshots of each in both themes.
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
  frames,
  page,
  reload,
  setView,
  showChrome,
  tag,
  until: waitFor,
  watchCsp,
} = require("./smoke-kit.cjs");

const SESSION = 'aside [aria-label="Session"]';
const GUEST = `[...document.querySelectorAll('${SESSION} li')]`;
const sidebarTitle = (title) => `document.querySelector('aside h2')?.textContent === '${title}'`;
const follow =
  "[...document.querySelectorAll('header button')].find((b) => b.textContent === 'Follow presenter')";
// A point in the element with this cursor id, by fractions of its box.
const within = (id, fx, fy) =>
  `(() => { const r = document.querySelector('[data-cursor-id="${id}"]').getBoundingClientRect(); return { x: Math.round(r.left + r.width * ${fx}), y: Math.round(r.top + r.height * ${fy}) }; })()`;

module.exports.run = async ({ window, shell, ownerCsp }) => {
  const { js, wait } = page(window);
  const owner = { js, wait };
  const shoot = async (name, theme, windows) => {
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
  const shootBoth = async (name, windows) => {
    for (const theme of ["light", "dark"]) await shoot(name, theme, windows);
  };
  // The page must fit the smallest window at 200% zoom in this state; the picture is kept for review.
  const fitsAtMinimum = async (win, driver, name) => {
    const bounds = win.getBounds();
    await setView(win, [640, 480], 2);
    await showChrome(win);
    assert.deepEqual(
      await driver.js(`(${fit.page.toString()})()`),
      [],
      `${name} must fit at 200% zoom`,
    );
    await shootBoth(`${name}-200`, [["", win, driver]]);
    win.webContents.setZoomFactor(1);
    win.setBounds(bounds);
  };

  // Sharing is off until someone asks: the window's Session is private, and the backend lists none. The title bar's
  // Start sharing turns it on and shows the Sessions view, with the join link a second window opens as a watcher.
  window.webContents.setZoomFactor(1);
  await js(
    "document.querySelector('[role=menu]') && document.querySelector('[aria-expanded=true]').click()",
  );
  window.setBounds({ x: 0, y: 0, width: 1000, height: 700 });
  const sessions = () => js("fetch('/api/sessions').then((r) => r.json())");
  assert.deepEqual(await sessions(), []);
  await click(window, "share");
  const joinLink = "document.querySelector('aside input[aria-label=\"Join link\"]')";
  await wait(joinLink);
  const link = await js(`${joinLink}.value`);
  assert.match(link, /^http:\/\/127\.0\.0\.1:\d+\/#join=/);
  assert.equal((await sessions()).length, 1);

  const other = await shell.showRemote(link);
  other.setBounds({ x: 40, y: 40, width: 760, height: 560 });
  const second = page(other);
  await second.wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  // The watcher shows the owner's view: the Sessions view with both of them in it, and the owner's open video.
  const participants = `${GUEST}.length`;
  await second.wait(sidebarTitle("Sessions"));
  await second.wait(`${participants} === 2`);
  await wait(`${participants} === 2`);
  const panes =
    "[...document.querySelectorAll('[role=group][data-cursor-id^=\"pane:\"]')].map((pane) => pane.getAttribute('aria-label'))";
  await second.wait(`${panes}.join() === 'long_gop_h264.mp4'`);
  await second.wait("document.querySelector('input[aria-label=Frame]')");
  assert.equal(await second.js(`${panes}.join()`), await js(`${panes}.join()`));

  // Reloading the watcher takes its seat back: same Guest N, and the owner still sees two participants.
  const watcherCsp = watchCsp(other);
  const you = `${GUEST}.find((li) => li.textContent.includes('(you)'))?.textContent`;
  const guest = await second.js(you);
  assert.match(guest, /^Guest \d+ \(you\)$/);
  await reload(other);
  await second.wait(`${participants} === 2`);
  assert.equal(await second.js(you), guest, "A reload must keep the watcher's seat");
  await delay(500);
  assert.equal(await js(participants), 2);
  const both = [
    ["-window-1", window, owner],
    ["-window-2", other, second],
  ];
  await shootBoth("sessions-view", both);

  // The share menu holds the cursor options and the way out.
  await js("document.querySelector('header [aria-label=Session]').click()");
  await wait("document.querySelector('[role=menu]')");
  await shootBoth("share-menu", [["-window-1", window, owner]]);
  await js("document.querySelector('header [aria-label=Session]').click()");
  await wait("!document.querySelector('[role=menu]')");

  // A following watcher's clicks change nothing: its controls are disabled, it cannot close the owner's pane, and
  // the owner's window is as it was.
  const ownerView = () =>
    js(`JSON.stringify({
      title: document.querySelector('aside h2')?.textContent,
      pressed: [...document.querySelectorAll('nav[aria-label=Activity] [aria-pressed=true]')].map((b) => b.ariaLabel),
      explore: document.querySelector('aside input[type=checkbox]').checked,
      panes: document.querySelectorAll('[role=group][data-cursor-id^="pane:"]').length,
    })`);
  const ownerBefore = await ownerView();
  assert.equal(await second.js("document.querySelector('[aria-label=Bookmarks]').disabled"), true);
  const close = "document.querySelector('[data-cursor-id^=\"pane-close:\"]')";
  assert.equal(await second.js(`${close}.disabled`), true);
  const closeId = await second.js(`${close}.dataset.cursorId`);
  for (const id of ["activity:bookmarks", "activity:live", "activity:workspace", closeId])
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
  assert.ok(
    await js(`Boolean(${sidebarTitle("Sessions")})`),
    "Exploring must not change the owner's view",
  );
  await shootBoth("session-exploring", [["", other, second]]);
  // The owner sees which guest explores, in the list and in the title bar's share indicator.
  await wait(`${GUEST}.filter((li) => li.textContent.includes('Exploring')).length === 1`);
  await wait("document.querySelector('header [aria-label=Session]').title.endsWith('1 exploring')");
  await shootBoth("session-exploring-owner", [["", window, owner]]);
  await fitsAtMinimum(other, second, "session-exploring");
  await second.js(`${follow}.click()`);
  await second.wait(sidebarTitle("Sessions"));
  await second.wait(`!${follow}`);
  assert.equal((await ownerView()).includes('"explore":true'), true);
  await js(explore);

  // The owner's choice of view is the watcher's, and the watcher cannot make its own.
  await js("document.querySelector('[aria-label=Live]').click()");
  await second.wait(sidebarTitle("Live"));
  assert.equal(await second.js("document.querySelector('[aria-label=Bookmarks]').disabled"), true);

  // The owner's pointer over a control draws a cursor on the same control in the watcher's narrower window, and
  // over the video it lands on the same picture pixel although the two windows show the picture at other sizes.
  const cursorAt = async (id, spot) => {
    window.webContents.sendInputEvent({ type: "mouseLeave", x: 0, y: 0 });
    await second.wait(`!${tag("Guest 1")}`);
    window.webContents.sendInputEvent({ type: "mouseMove", x: spot.x - 3, y: spot.y });
    window.webContents.sendInputEvent({ type: "mouseMove", x: spot.x, y: spot.y });
    await second.wait(tag("Guest 1"));
    const expected = await second.js(
      id.startsWith("frame:") ? within(id, spot.fx, spot.fy) : center(id),
    );
    let drawn;
    await waitFor(
      async () => {
        drawn = await second.js(
          `(() => { const r = ${tag("Guest 1")}.parentElement.getBoundingClientRect(); return { x: r.left, y: r.top }; })()`,
        );
        return Math.abs(drawn.x - expected.x) <= 2 && Math.abs(drawn.y - expected.y) <= 2;
      },
      `the cursor on ${id} (drawn at ${JSON.stringify(drawn)}, expected ${JSON.stringify(expected)})`,
    );
  };
  for (const id of ["menu:View", "activity:sessions", "activity:bookmarks"]) {
    await cursorAt(id, await js(center(id)));
    if (id === "menu:View") await shootBoth("session-cursor-menu", both);
  }
  // Every video draws through the lane grid, which names the picture of its lane 0 `:0`.
  const picture = await js(
    'document.querySelector(\'[data-cursor-id^="frame:"][data-cursor-id$=":0"]\').dataset.cursorId',
  );
  await cursorAt(picture, { ...(await js(within(picture, 0.25, 0.3))), fx: 0.25, fy: 0.3 });
  await shootBoth("session-cursor-video", both);

  // A third window on the same backend is a Session of its own and lists the shared one to watch.
  window.webContents.sendInputEvent({ type: "mouseLeave", x: 0, y: 0 });
  await second.wait(`!${tag("Guest 1")}`);
  const third = await shell.showLocal();
  third.setBounds({ x: 80, y: 80, width: 760, height: 560 });
  const listing = page(third);
  await listing.wait("document.querySelector('h1')?.textContent === 'ax-devil'");
  await listing.js("document.querySelector('[aria-label=Sessions]').click()");
  await listing.wait("document.querySelector('aside [aria-label=\"Shared sessions\"] button')");
  assert.equal(
    await listing.js(
      "document.querySelector('aside [aria-label=\"Shared sessions\"] li').textContent.includes('2')",
    ),
    true,
  );
  await shootBoth("sessions-list", [["", third, listing]]);
  await fitsAtMinimum(third, listing, "sessions-list");
  third.close();

  // Stopping tells the watcher, and the owner's Session is private again.
  await js("document.querySelector('[aria-label=Sessions]').click()");
  await wait(button("Stop sharing"));
  await js(`${button("Stop sharing")}.click()`);
  await second.wait(
    "document.querySelector('[role=alert]')?.textContent.includes('stopped sharing')",
  );
  await shootBoth("session-ended", [["", other, second]]);
  await wait(button("Start sharing"));
  assert.deepEqual(await sessions(), []);
  assert.deepEqual([ownerCsp, watcherCsp], [[], []], "The app must break no CSP rule");
  other.close();
};
